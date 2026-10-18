"""条码业务服务：生成、批量、编号范围分配、导入校验、状态机。

编码来源两种模式（规格 3.3）：
- internal        : 内部管理/测试编码，仅本地去重；不得宣称为合法 GS1 商品编码；
- gs1_authorized  : 用户合法取得的 GS1 授权范围，必须提供授权来源编号。

本地去重只证明"本地未重复"，不代表全球唯一或已注册。
"""

from ..barcode.checkdigit import (
    BARCODE_LENGTHS,
    BarcodeValidationError,
    calculate_check_digit,
    normalize,
)
from ..database.dao import (
    BarcodeRecordDAO,
    NumberingRangeDAO,
    OperationLogDAO,
)


class BarcodeError(Exception):
    """条码业务错误。"""


def _wrap_validation(exc):
    raise BarcodeError(str(exc)) from exc


class BarcodeService:
    def __init__(self, db):
        self.db = db
        self.records = BarcodeRecordDAO(db)
        self.ranges = NumberingRangeDAO(db)
        self.logs = OperationLogDAO(db)

    # ---------- 编号范围 ----------
    def create_range(self, range_name, barcode_type, prefix, start_number,
                     end_number, *, source_type="internal",
                     authorization_reference=None):
        """创建编号范围。

        start_number / end_number 为**基础编号**（不含校验位）；
        每个基础编号分配时按格式规则计算校验位，生成合法 GTIN。
        """
        if barcode_type not in BARCODE_LENGTHS:
            raise BarcodeError(f"不支持的条码类型: {barcode_type}")
        if source_type not in ("internal", "gs1_authorized"):
            raise BarcodeError(f"非法的编码来源类型: {source_type}")
        if source_type == "gs1_authorized" and not authorization_reference:
            raise BarcodeError("GS1 授权编码范围必须提供授权来源编号（authorization_reference）")
        prefix = prefix or ""
        if prefix and any(not ("0" <= c <= "9") for c in prefix):
            raise BarcodeError("编号前缀必须为数字")
        width = BARCODE_LENGTHS[barcode_type] - len(prefix) - 1
        if width <= 0:
            raise BarcodeError("前缀长度超出该条码格式的容量")
        if start_number < 0 or end_number < start_number:
            raise BarcodeError("编号范围无效：结束值必须大于等于起始值")
        if end_number > 10 ** width - 1:
            raise BarcodeError(
                f"结束编号超出 {barcode_type} 在前缀 {prefix!r} 下的基础编号容量上限 {10 ** width - 1}"
            )
        with self.db.transaction(immediate=True) as conn:
            rid = self.ranges.insert(
                range_name=range_name,
                barcode_type=barcode_type,
                source_type=source_type,
                prefix=prefix,
                start_number=start_number,
                end_number=end_number,
                next_number=start_number,
                authorization_reference=authorization_reference,
                range_status="active",
                conn=conn,
            )
            self.logs.insert(
                "create_range",
                f"{prefix}[{start_number}-{end_number}]",
                "success",
                conn=conn,
            )
        return rid

    def get_range(self, range_id):
        return self.ranges.get(range_id)

    def list_ranges(self):
        return self.ranges.list()

    # ---------- 生成 ----------
    def _format_range_value(self, rng):
        """范围内分配：基础编号 + 按规则计算的校验位 = 合法完整编码。"""
        prefix = rng["prefix"] or ""
        width = BARCODE_LENGTHS[rng["barcode_type"]] - len(prefix) - 1
        if width <= 0:
            raise BarcodeError("范围内前缀长度超出条码格式容量")
        num = rng["next_number"]
        if num < rng["start_number"] or num > rng["end_number"]:
            raise BarcodeError("范围内 next_number 越界，数据可能已损坏")
        base = f"{prefix}{num:0{width}d}"
        return base + calculate_check_digit(base, rng["barcode_type"])

    def _allocate_from_range(self, rng, conn, product_name=None, notes=None):
        value = self._format_range_value(rng)
        if self.records.exists(value, conn=conn):
            raise BarcodeError(f"编号范围内编码 {value} 与已有记录冲突，已停止分配")
        self.records.insert(
            barcode_value=value,
            barcode_type=rng["barcode_type"],
            source_type=rng["source_type"],
            allocation_status="generated",
            product_name=product_name,
            notes=notes,
            range_id=rng["id"],
            conn=conn,
        )
        next_number = rng["next_number"] + 1
        if next_number > rng["end_number"]:
            self.ranges.set_status(rng["id"], "exhausted", conn=conn)
        else:
            self.ranges.set_next_number(rng["id"], next_number, conn=conn)
        rng["next_number"] = next_number
        self.logs.insert("generate", value, "success", conn=conn)
        return self.records.get(value, conn=conn)

    def generate_single(self, barcode_type, *, explicit_value=None, range_id=None,
                        source_type="internal", product_name=None, notes=None):
        """生成单个编码。必须且只能提供 explicit_value 或 range_id 之一。"""
        if (explicit_value is None) == (range_id is None):
            raise BarcodeError("必须且只能提供 explicit_value 或 range_id 之一")
        try:
            with self.db.transaction(immediate=True) as conn:
                if range_id is not None:
                    rng = self.ranges.get(range_id, conn=conn)
                    if not rng:
                        raise BarcodeError(f"编号范围 {range_id} 不存在")
                    if rng["range_status"] != "active":
                        raise BarcodeError(
                            f"编号范围 {range_id} 状态为 {rng['range_status']}，无法继续分配"
                        )
                    if rng["barcode_type"] != barcode_type:
                        raise BarcodeError("编号范围的条码类型与请求类型不一致")
                    return self._allocate_from_range(rng, conn, product_name, notes)
                value = normalize(explicit_value, barcode_type)
                if self.records.exists(value, conn=conn):
                    raise BarcodeError(f"编码 {value} 已存在，不能重复分配")
                self.records.insert(
                    barcode_value=value,
                    barcode_type=barcode_type,
                    source_type=source_type,
                    allocation_status="generated",
                    product_name=product_name,
                    notes=notes,
                    conn=conn,
                )
                self.logs.insert("generate", value, "success", conn=conn)
                return self.records.get(value, conn=conn)
        except BarcodeValidationError as exc:
            _wrap_validation(exc)

    def generate_batch(self, barcode_type, *, count, range_id,
                       product_name=None, notes=None):
        """从编号范围批量生成。

        范围耗尽时安全停止：返回已生成部分，exhausted=True，
        范围状态置为 exhausted，不会产生重复或越界编码。
        """
        if not isinstance(count, int) or isinstance(count, bool) or count < 1:
            raise BarcodeError("批量数量必须为正整数")
        created = []
        with self.db.transaction(immediate=True) as conn:
            rng = self.ranges.get(range_id, conn=conn)
            if not rng:
                raise BarcodeError(f"编号范围 {range_id} 不存在")
            if rng["range_status"] != "active":
                raise BarcodeError(
                    f"编号范围 {range_id} 状态为 {rng['range_status']}，无法继续分配"
                )
            if rng["barcode_type"] != barcode_type:
                raise BarcodeError("编号范围的条码类型与请求类型不一致")
            exhausted = False
            for _ in range(count):
                if rng["next_number"] > rng["end_number"]:
                    self.ranges.set_status(range_id, "exhausted", conn=conn)
                    exhausted = True
                    break
                created.append(self._allocate_from_range(rng, conn, product_name, notes))
        return {"created": created, "requested": count, "exhausted": exhausted}

    # ---------- 导入 ----------
    def validate_import(self, values, barcode_type):
        """批量导入预检：长度 / 字符 / 校验位 / 文件内重复 / 库内重复。"""
        report = {
            "barcode_type": barcode_type,
            "total": len(values),
            "valid": [],
            "invalid": [],
            "duplicates_in_file": [],
            "already_exists": [],
        }
        seen = set()
        for raw in values:
            text = (raw or "").strip()
            try:
                value = normalize(text, barcode_type)
            except BarcodeValidationError as exc:
                report["invalid"].append({"value": raw, "reason": str(exc)})
                continue
            if value in seen:
                report["duplicates_in_file"].append({"value": raw, "reason": "与导入清单中其他编码重复"})
                continue
            seen.add(value)
            if self.records.exists(value):
                report["already_exists"].append({"value": raw, "reason": "本地数据库已存在该编码"})
                continue
            report["valid"].append(value)
        return report

    def import_records(self, values, barcode_type, *, source_type="import",
                       product_name=None, notes=None):
        report = self.validate_import(values, barcode_type)
        with self.db.transaction(immediate=True) as conn:
            for value in report["valid"]:
                self.records.insert(
                    barcode_value=value,
                    barcode_type=barcode_type,
                    source_type=source_type,
                    allocation_status="generated",
                    product_name=product_name,
                    notes=notes,
                    conn=conn,
                )
                self.logs.insert("import", value, "success", conn=conn)
        return {"imported": len(report["valid"]), "report": report}

    # ---------- 状态机 ----------
    def allocate(self, value):
        """generated -> allocated"""
        with self.db.transaction(immediate=True) as conn:
            rec = self.records.get(value, conn=conn)
            if not rec:
                raise BarcodeError(f"编码 {value} 不存在")
            if rec["allocation_status"] != "generated":
                raise BarcodeError(
                    f"编码 {value} 当前状态为 {rec['allocation_status']}，不能分配"
                )
            self.records.update_status(value, "allocated", conn=conn)
            self.logs.insert("allocate", value, "success", conn=conn)
            return self.records.get(value, conn=conn)

    def mark_used(self, value):
        """generated/allocated -> used"""
        with self.db.transaction(immediate=True) as conn:
            rec = self.records.get(value, conn=conn)
            if not rec:
                raise BarcodeError(f"编码 {value} 不存在")
            if rec["allocation_status"] not in ("generated", "allocated"):
                raise BarcodeError(
                    f"编码 {value} 当前状态为 {rec['allocation_status']}，不能标记为已使用"
                )
            self.records.update_status(value, "used", conn=conn)
            self.logs.insert("mark_used", value, "success", conn=conn)
            return self.records.get(value, conn=conn)

    def invalidate(self, value, *, reason=None):
        """作废编码：保留历史记录，不得重新分配。"""
        with self.db.transaction(immediate=True) as conn:
            rec = self.records.get(value, conn=conn)
            if not rec:
                raise BarcodeError(f"编码 {value} 不存在")
            if rec["allocation_status"] == "invalidated":
                raise BarcodeError(f"编码 {value} 已作废")
            self.records.update_status(value, "invalidated", conn=conn)
            self.logs.insert("invalidate", value, reason or "no reason", conn=conn)
            return self.records.get(value, conn=conn)

    def update_details(self, value, *, product_name=None, notes=None):
        """仅 generated 状态可修改商品名称/备注。"""
        ok = self.records.update_details(value, product_name=product_name, notes=notes)
        if not ok:
            raise BarcodeError(f"编码 {value} 不存在或已分配/作废，不能修改详情")
        self.logs.insert("update_details", value, "success")
        return self.records.get(value)

    # ---------- 查询 ----------
    def query_records(self, **filters):
        return self.records.query(**filters)

    def get_record(self, value):
        return self.records.get(value)

    def get_history(self, value):
        return self.logs.list_for_value(value)

    def get_stats(self):
        return {
            "total": sum(self.records.count_by_status().values()),
            "by_status": self.records.count_by_status(),
            "ranges_active": sum(
                1 for r in self.ranges.list() if r["range_status"] == "active"
            ),
            "ranges_exhausted": sum(
                1 for r in self.ranges.list() if r["range_status"] == "exhausted"
            ),
        }
