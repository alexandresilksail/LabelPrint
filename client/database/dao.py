"""数据访问对象：全部使用参数化 SQL，禁止字符串拼接。"""

from .db import utc_now_iso


def _conn_of(db, conn):
    return conn if conn is not None else db.conn


class BarcodeRecordDAO:
    def __init__(self, db):
        self.db = db

    def insert(self, *, barcode_value, barcode_type, source_type="internal",
               allocation_status="generated", product_name=None, notes=None,
               range_id=None, authorization_reference=None, conn=None):
        now = utc_now_iso()
        cur = _conn_of(self.db, conn).execute(
            """INSERT INTO barcode_records
               (barcode_value, barcode_type, source_type, authorization_reference,
                allocation_status, product_name, notes, range_id,
                created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (barcode_value, barcode_type, source_type, authorization_reference,
             allocation_status, product_name, notes, range_id, now, now),
        )
        return cur.lastrowid

    def exists(self, value, *, conn=None):
        row = _conn_of(self.db, conn).execute(
            "SELECT 1 FROM barcode_records WHERE barcode_value = ?", (value,)
        ).fetchone()
        return row is not None

    def get(self, value, *, conn=None):
        row = _conn_of(self.db, conn).execute(
            "SELECT * FROM barcode_records WHERE barcode_value = ?", (value,)
        ).fetchone()
        return dict(row) if row else None

    def update_status(self, value, status, *, conn=None):
        now = utc_now_iso()
        c = _conn_of(self.db, conn)
        if status == "invalidated":
            cur = c.execute(
                """UPDATE barcode_records
                   SET allocation_status = ?, updated_at = ?, invalidated_at = ?
                   WHERE barcode_value = ? AND allocation_status != 'invalidated'""",
                (status, now, now, value),
            )
        else:
            cur = c.execute(
                """UPDATE barcode_records
                   SET allocation_status = ?, updated_at = ?
                   WHERE barcode_value = ?""",
                (status, now, value),
            )
        return cur.rowcount == 1

    def update_details(self, value, *, product_name=None, notes=None, conn=None):
        """仅允许修改 generated 状态的商品名称/备注；已分配编码不可改内容。"""
        cur = _conn_of(self.db, conn).execute(
            """UPDATE barcode_records
               SET product_name = ?, notes = ?, updated_at = ?
               WHERE barcode_value = ? AND allocation_status = 'generated'""",
            (product_name, notes, utc_now_iso(), value),
        )
        return cur.rowcount == 1

    def query(self, *, value=None, product_name=None, barcode_type=None,
              status=None, created_from=None, created_to=None,
              limit=200, offset=0, conn=None):
        sql = "SELECT * FROM barcode_records WHERE 1 = 1"
        params = []
        if value:
            sql += " AND barcode_value LIKE ?"
            params.append(f"%{value}%")
        if product_name:
            sql += " AND product_name LIKE ?"
            params.append(f"%{product_name}%")
        if barcode_type:
            sql += " AND barcode_type = ?"
            params.append(barcode_type)
        if status:
            sql += " AND allocation_status = ?"
            params.append(status)
        if created_from:
            sql += " AND created_at >= ?"
            params.append(created_from)
        if created_to:
            sql += " AND created_at <= ?"
            params.append(created_to)
        sql += " ORDER BY id DESC LIMIT ? OFFSET ?"
        params += [int(limit), int(offset)]
        rows = _conn_of(self.db, conn).execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def count_by_status(self, *, conn=None):
        rows = _conn_of(self.db, conn).execute(
            "SELECT allocation_status, COUNT(*) AS n FROM barcode_records GROUP BY allocation_status"
        ).fetchall()
        return {r["allocation_status"]: r["n"] for r in rows}


class NumberingRangeDAO:
    def __init__(self, db):
        self.db = db

    def insert(self, *, range_name, barcode_type, source_type, prefix,
               start_number, end_number, next_number,
               authorization_reference=None, range_status="active", conn=None):
        now = utc_now_iso()
        cur = _conn_of(self.db, conn).execute(
            """INSERT INTO numbering_ranges
               (range_name, barcode_type, source_type, prefix, start_number,
                end_number, next_number, authorization_reference, range_status,
                created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (range_name, barcode_type, source_type, prefix, start_number,
             end_number, next_number, authorization_reference, range_status,
             now, now),
        )
        return cur.lastrowid

    def get(self, range_id, *, conn=None):
        row = _conn_of(self.db, conn).execute(
            "SELECT * FROM numbering_ranges WHERE id = ?", (range_id,)
        ).fetchone()
        return dict(row) if row else None

    def list(self, *, conn=None):
        rows = _conn_of(self.db, conn).execute(
            "SELECT * FROM numbering_ranges ORDER BY id"
        ).fetchall()
        return [dict(r) for r in rows]

    def set_next_number(self, range_id, next_number, *, conn=None):
        _conn_of(self.db, conn).execute(
            "UPDATE numbering_ranges SET next_number = ?, updated_at = ? WHERE id = ?",
            (next_number, utc_now_iso(), range_id),
        )

    def set_status(self, range_id, range_status, *, conn=None):
        _conn_of(self.db, conn).execute(
            "UPDATE numbering_ranges SET range_status = ?, updated_at = ? WHERE id = ?",
            (range_status, utc_now_iso(), range_id),
        )


class PrintTemplateDAO:
    def __init__(self, db):
        self.db = db

    def list(self, *, conn=None):
        rows = _conn_of(self.db, conn).execute(
            "SELECT * FROM print_templates ORDER BY id"
        ).fetchall()
        return [dict(r) for r in rows]

    def get(self, template_id, *, conn=None):
        row = _conn_of(self.db, conn).execute(
            "SELECT * FROM print_templates WHERE id = ?", (template_id,)
        ).fetchone()
        return dict(row) if row else None

    def get_by_name(self, name, *, conn=None):
        row = _conn_of(self.db, conn).execute(
            "SELECT * FROM print_templates WHERE template_name = ?", (name,)
        ).fetchone()
        return dict(row) if row else None

    _COLS = (
        "template_name", "paper_width", "paper_height", "label_width",
        "label_height", "rows", "columns", "margins", "horizontal_gap",
        "vertical_gap", "orientation", "barcode_height", "show_text",
        "copies", "offset_x", "offset_y", "builtin",
    )

    # SQL 语句在类定义期由常量列名构建一次（列名来自本类常量，
    # 不来自任何外部输入；所有值一律参数化绑定），避免运行时拼串。
    _INSERT_SQL = (
        "INSERT INTO print_templates ("
        + ", ".join(_COLS)  # nosec B608 - 类常量列名，取值全部参数化
        + ", created_at, updated_at) VALUES ("
        + ", ".join("?" for _ in _COLS)
        + ", ?, ?)"
    )
    _UPDATE_SQL = (
        "UPDATE print_templates SET "
        + ", ".join(f"{c} = ?" for c in _COLS)  # nosec B608 - 同上
        + ", updated_at = ? WHERE id = ?"
    )

    def insert(self, template, *, conn=None):
        """template: LabelTemplate 实例或 to_dict() 结果。"""
        d = template.to_dict() if hasattr(template, "to_dict") else dict(template)
        now = utc_now_iso()
        params = [d.get(c) for c in self._COLS] + [now, now]
        # nosec B608 - 语句由类常量列名构建，全部取值参数化绑定
        cur = _conn_of(self.db, conn).execute(self._INSERT_SQL, params)
        return cur.lastrowid

    def update(self, template_id, template, *, conn=None):
        d = template.to_dict() if hasattr(template, "to_dict") else dict(template)
        params = [d.get(c) for c in self._COLS] + [utc_now_iso(), template_id]
        # nosec B608 - 语句由类常量列名构建，全部取值参数化绑定
        cur = _conn_of(self.db, conn).execute(self._UPDATE_SQL, params)
        return cur.rowcount == 1

    def delete(self, template_id, *, conn=None):
        cur = _conn_of(self.db, conn).execute(
            "DELETE FROM print_templates WHERE id = ? AND builtin = 0",
            (template_id,),
        )
        return cur.rowcount == 1

    def copy(self, template_id, new_name, *, conn=None):
        """复制模板（含预置模板）为新名称。"""
        src = self.get(template_id, conn=conn)
        if not src:
            raise ValueError(f"模板 {template_id} 不存在")
        if self.get_by_name(new_name, conn=conn):
            raise ValueError(f"模板名称已存在: {new_name}")
        src = dict(src)
        src.pop("id", None)
        src.pop("created_at", None)
        src.pop("updated_at", None)
        src["template_name"] = new_name
        src["builtin"] = 0
        return self.insert(src, conn=conn)


class OperationLogDAO:
    def __init__(self, db):
        self.db = db

    def insert(self, operation_type, barcode_value, result, *, conn=None):
        _conn_of(self.db, conn).execute(
            """INSERT INTO operation_logs
               (operation_type, barcode_value, operation_time, operation_result)
               VALUES (?, ?, ?, ?)""",
            (operation_type, barcode_value, utc_now_iso(), result),
        )

    def list_for_value(self, value, *, limit=100, conn=None):
        rows = _conn_of(self.db, conn).execute(
            """SELECT * FROM operation_logs
               WHERE barcode_value = ?
               ORDER BY id DESC LIMIT ?""",
            (value, int(limit)),
        ).fetchall()
        return [dict(r) for r in rows]
