"""按打印机独立保存/恢复打印配置（纯本地 JSON，不依赖系统 API）。

每台打印机一个 JSON 文件，互不影响；加载采用容错策略：非法字段
回退默认值、损坏 JSON 返回 None 并记日志，绝不向 UI 抛异常。

文件命名：``sanitize(printer_name) + ".json"``，清洗 Windows 非法文件名字符
（\\ / : * ? " < > |）与 ASCII 控制字符；清洗后为空回退到 ``"printer"``。
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .capabilities import PaperSize, PrintQuality

logger = logging.getLogger(__name__)

#: 偏移修正物理上限（mm），超过视为脏数据回退 0
MAX_OFFSET_MM = 500.0

_INVALID_FILENAME = re.compile(r'[\\/:*?"<>|\x00-\x1f\x7f]')


def _sanitize_filename(printer_name: str) -> str:
    """清洗打印机名为安全文件名（不含扩展名）。"""
    cleaned = _INVALID_FILENAME.sub("_", str(printer_name or "")).strip()
    cleaned = cleaned.strip("._ ")
    return cleaned or "printer"


def _finite_float(value, default: float) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    if v != v or v in (float("inf"), float("-inf")):
        return default
    return v


def _optional_nonneg(value) -> float | None:
    """可选非负浮点：非法/负数 → None。"""
    if value is None:
        return None
    v = _finite_float(value, float("nan"))
    if v != v or v < 0:
        return None
    return v


@dataclass
class PrinterPrintConfig:
    """单台打印机的打印配置（持久化单元）。

    容错加载约定（from_dict）：
    - 纸张非正尺寸 / 非法 → 回退 None；
    - |offset| > 500mm → 回退 0.0；
    - dpi 非正 → 回退 None；
    - 质量枚举非法 → 回退 NORMAL；份数 <1 → 1；间隙为负 → 0.0。
    所有非法值静默回退，不向 UI 抛异常。
    """

    printer_name: str
    offset_x_mm: float = 0.0
    offset_y_mm: float = 0.0
    paper: PaperSize | None = None
    dpi: int | None = None
    quality: PrintQuality = PrintQuality.NORMAL
    default_copies: int = 1
    #: 标签纸相邻标签的物理间隙（走纸/定位硬件参数，仅专用协议 TSPL GAP
    #: 等与设备相关）；**不是**条码静区，禁止进入条码布局或静区换算
    label_gap_mm: float = 0.0
    #: 条码每侧静区物理宽度（mm，UI/配置层单位）；渲染/编码层经 units
    #: 换算为模块数；None = 用 GS1 默认 11 模块 × 模块宽
    quiet_zone_mm: float | None = None
    #: 条码模块宽度（mm）；None = 由渲染 fit 取默认/自由适配
    module_mm: float | None = None
    use_calibration: bool = False
    notes: str = ""
    version: int = 1
    updated_at: str = ""

    # ---------------- 序列化 ----------------
    def to_dict(self) -> dict:
        return {
            "printer_name": self.printer_name,
            "offset_x_mm": self.offset_x_mm,
            "offset_y_mm": self.offset_y_mm,
            "paper": self.paper.to_dict() if self.paper else None,
            "dpi": self.dpi,
            "quality": self.quality.value,
            "default_copies": self.default_copies,
            "label_gap_mm": self.label_gap_mm,
            "quiet_zone_mm": self.quiet_zone_mm,
            "module_mm": self.module_mm,
            "use_calibration": bool(self.use_calibration),
            "notes": str(self.notes or ""),
            "version": int(self.version),
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "PrinterPrintConfig":
        """容错构造：缺省字段回退默认；非法值回退默认，不抛异常。"""
        if not isinstance(data, dict):
            data = {}

        # ---- 偏移：|offset| > 500mm 回退 0 ----
        off_x = _finite_float(data.get("offset_x_mm", 0.0), 0.0)
        off_y = _finite_float(data.get("offset_y_mm", 0.0), 0.0)
        if abs(off_x) > MAX_OFFSET_MM:
            off_x = 0.0
        if abs(off_y) > MAX_OFFSET_MM:
            off_y = 0.0

        # ---- 纸张：非正尺寸回退 None ----
        paper = None
        raw_paper = data.get("paper")
        if isinstance(raw_paper, dict):
            cand = PaperSize.from_dict(raw_paper)
            if cand.is_valid:
                paper = cand

        # ---- DPI：非正回退 None ----
        dpi_raw = data.get("dpi")
        dpi: int | None = None
        if dpi_raw is not None:
            try:
                dpi_val = int(dpi_raw)
                if dpi_val > 0:
                    dpi = dpi_val
            except (TypeError, ValueError):
                dpi = None

        # ---- 质量枚举：非法回退 NORMAL ----
        quality = PrintQuality.NORMAL
        raw_q = data.get("quality")
        try:
            quality = PrintQuality(raw_q)
        except (ValueError, TypeError):
            quality = PrintQuality.NORMAL

        # ---- 份数：<1 回退 1 ----
        try:
            copies = int(data.get("default_copies", 1))
        except (TypeError, ValueError):
            copies = 1
        if copies < 1:
            copies = 1

        gap = _finite_float(data.get("label_gap_mm", 0.0), 0.0)
        if gap < 0:
            gap = 0.0

        return cls(
            printer_name=str(data.get("printer_name", "")),
            offset_x_mm=off_x,
            offset_y_mm=off_y,
            paper=paper,
            dpi=dpi,
            quality=quality,
            default_copies=copies,
            label_gap_mm=gap,
            quiet_zone_mm=_optional_nonneg(data.get("quiet_zone_mm")),
            module_mm=_optional_nonneg(data.get("module_mm")),
            use_calibration=bool(data.get("use_calibration", False)),
            notes=str(data.get("notes", "") or ""),
            version=int(data.get("version", 1) or 1),
            updated_at=str(data.get("updated_at", "") or ""),
        )


class PrinterConfigStore:
    """打印机配置仓库：每台打印机一个 JSON，原子写。"""

    def __init__(self, base_dir: Path | str):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    # ---------------- 路径 ----------------
    def _path(self, printer_name: str) -> Path:
        return self.base_dir / f"{_sanitize_filename(printer_name)}.json"

    # ---------------- 读 ----------------
    def load(self, printer_name: str) -> PrinterPrintConfig | None:
        """加载某台打印机配置；文件不存在 → None；损坏 JSON → None 并 warning。"""
        path = self._path(printer_name)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            logger.warning("打印机配置损坏，忽略并回退默认：%s（%s）",
                           path, exc)
            return None
        cfg = PrinterPrintConfig.from_dict(data)
        if not cfg.printer_name:
            cfg.printer_name = printer_name
        return cfg

    def load_all(self) -> list[PrinterPrintConfig]:
        """加载仓库内全部可用配置（损坏文件跳过）。"""
        out: list[PrinterPrintConfig] = []
        for p in sorted(self.base_dir.glob("*.json")):
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                logger.warning("跳过损坏配置文件 %s（%s）", p, exc)
                continue
            cfg = PrinterPrintConfig.from_dict(data)
            if not cfg.printer_name:
                cfg.printer_name = p.stem
            out.append(cfg)
        return out

    def list_printers(self) -> list[str]:
        """已保存配置的打印机名列表（按文件名排序）。"""
        names: list[str] = []
        for p in sorted(self.base_dir.glob("*.json")):
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                name = str(data.get("printer_name") or p.stem)
            except (OSError, ValueError):
                name = p.stem
            names.append(name)
        return names

    # ---------------- 写 ----------------
    def save(self, config: PrinterPrintConfig) -> Path:
        """原子写：先写同目录临时文件 + fsync，再 os.replace 落位。"""
        if not config.updated_at:
            config.updated_at = datetime.now(timezone.utc).isoformat(
                timespec="seconds")
        target = self._path(config.printer_name)
        payload = json.dumps(config.to_dict(), ensure_ascii=False, indent=2)

        fd, tmp = tempfile.mkstemp(
            prefix=target.name + ".", suffix=".tmp", dir=str(self.base_dir))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(payload)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, target)
        except BaseException:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass
            raise
        return target

    def reset(self, printer_name: str) -> bool:
        """删除配置文件 = 恢复默认；文件不存在返回 False。"""
        path = self._path(printer_name)
        if path.exists():
            try:
                path.unlink()
                return True
            except OSError as exc:
                logger.warning("删除打印机配置失败 %s（%s）", path, exc)
                return False
        return False

    # ---------------- 工厂 ----------------
    def defaults(self, printer_name: str) -> PrinterPrintConfig:
        """全默认值配置工厂（不写盘）。"""
        return PrinterPrintConfig(printer_name=str(printer_name))


def default_config_dir() -> Path:
    """平台默认配置目录。

    - Windows：``%LOCALAPPDATA%\\CodeLabelStudio\\printer_configs``
    - 其他平台（含 Linux/macOS）：``~/.codelabel/printer_configs``
    """
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(
            Path.home() / "AppData" / "Local")
        return Path(base) / "CodeLabelStudio" / "printer_configs"
    return Path.home() / ".codelabel" / "printer_configs"


def default_store() -> PrinterConfigStore:
    """模块级工厂：使用平台默认目录的配置仓库。"""
    return PrinterConfigStore(default_config_dir())
