"""打印机独立配置存储测试：round-trip、隔离、reset、损坏 JSON、
文件名清洗、原子写、defaults 工厂、容错加载。不依赖 PySide6/pywin32。"""

import json

import pytest

from client.printing.capabilities import PaperSize, PrintQuality
from client.printing.config_store import (
    PrinterConfigStore,
    PrinterPrintConfig,
)


@pytest.fixture()
def store(tmp_path):
    return PrinterConfigStore(tmp_path / "printer_configs")


class TestRoundTrip:
    def test_save_load_equals(self, store):
        cfg = PrinterPrintConfig(
            printer_name="Zebra LP2844",
            offset_x_mm=1.25,
            offset_y_mm=-0.75,
            paper=PaperSize("标签 40x25", 40.0, 25.0),
            dpi=203,
            quality=PrintQuality.HIGH,
            default_copies=3,
            label_gap_mm=3.0,
            quiet_zone_mm=3.63,
            module_mm=0.33,
            use_calibration=True,
            notes="已校准",
        )
        target = store.save(cfg)
        assert target.exists()

        loaded = store.load("Zebra LP2844")
        assert loaded is not None
        assert loaded.to_dict() == cfg.to_dict()

    def test_two_printers_isolated(self, store):
        a = PrinterPrintConfig("Printer-A", offset_x_mm=1.0, dpi=203)
        b = PrinterPrintConfig("Printer-B", offset_y_mm=2.0, dpi=300)
        store.save(a)
        store.save(b)

        la = store.load("Printer-A")
        lb = store.load("Printer-B")
        assert la.offset_x_mm == 1.0 and la.offset_y_mm == 0.0
        assert lb.offset_y_mm == 2.0 and lb.offset_x_mm == 0.0
        assert la.dpi == 203 and lb.dpi == 300
        assert sorted(store.list_printers()) == ["Printer-A", "Printer-B"]

    def test_load_all(self, store):
        store.save(PrinterPrintConfig("P1", dpi=203))
        store.save(PrinterPrintConfig("P2", dpi=300))
        all_cfgs = store.load_all()
        assert {c.printer_name for c in all_cfgs} == {"P1", "P2"}


class TestReset:
    def test_reset_returns_none(self, store):
        store.save(PrinterPrintConfig("Old", offset_x_mm=5.0))
        assert store.reset("Old") is True
        assert store.load("Old") is None
        assert store.reset("Old") is False

    def test_defaults_factory(self, store):
        cfg = store.defaults("NewPrinter")
        assert cfg.printer_name == "NewPrinter"
        assert cfg.offset_x_mm == 0.0
        assert cfg.offset_y_mm == 0.0
        assert cfg.paper is None
        assert cfg.dpi is None
        assert cfg.quality == PrintQuality.NORMAL
        assert cfg.default_copies == 1
        assert cfg.label_gap_mm == 0.0
        assert cfg.quiet_zone_mm is None
        assert cfg.module_mm is None
        assert cfg.use_calibration is False
        # 工厂不落盘
        assert store.load("NewPrinter") is None


class TestCorruptJson:
    def test_corrupt_file_returns_none(self, store, caplog):
        path = store._path("Broken")
        path.write_text("{not valid json", encoding="utf-8")
        assert store.load("Broken") is None
        assert store.load_all() == []

    def test_missing_file_returns_none(self, store):
        assert store.load("不存在的打印机") is None


class TestFilenameSanitize:
    def test_forbidden_chars_sanitized(self, store):
        nasty = 'a/b\\c:d*?"<>e\x01f'
        cfg = PrinterPrintConfig(nasty, offset_x_mm=0.5)
        store.save(cfg)
        # 目录内不出现任何非法字符
        files = [p.name for p in store.base_dir.glob("*.json")]
        assert len(files) == 1
        for ch in '\\/:*?"<>|':
            assert ch not in files[0]
        # 可按原名重新加载（文件内部记录原始名）
        loaded = store.load(nasty)
        assert loaded is not None
        assert loaded.printer_name == nasty
        assert store.list_printers() == [nasty]

    def test_empty_name_falls_back(self, store):
        store.save(PrinterPrintConfig("   ", dpi=203))
        files = [p.name for p in store.base_dir.glob("*.json")]
        assert files == ["printer.json"]


class TestAtomicWrite:
    def test_no_tmp_leftover_after_save(self, store):
        store.save(PrinterPrintConfig("Atom", dpi=203))
        leftovers = list(store.base_dir.glob("*.tmp"))
        assert leftovers == []
        assert store.load("Atom") is not None


class TestTolerantLoad:
    def test_invalid_values_fall_back(self, store):
        raw = {
            "printer_name": "Dirty",
            "offset_x_mm": 600.0,          # |offset| > 500 → 0
            "offset_y_mm": -600.0,
            "paper": {"name": "bad", "width_mm": 0.0, "height_mm": -5.0},
            "dpi": 0,                       # 非正 → None
            "quality": "bogus",             # 非法枚举 → NORMAL
            "default_copies": 0,            # <1 → 1
            "label_gap_mm": -2.0,           # 负 → 0
            "quiet_zone_mm": -1.0,          # 负 → None
            "module_mm": "abc",             # 非法 → None
            "use_calibration": "yes",
        }
        cfg = PrinterPrintConfig.from_dict(raw)
        assert cfg.offset_x_mm == 0.0
        assert cfg.offset_y_mm == 0.0
        assert cfg.paper is None
        assert cfg.dpi is None
        assert cfg.quality == PrintQuality.NORMAL
        assert cfg.default_copies == 1
        assert cfg.label_gap_mm == 0.0
        assert cfg.quiet_zone_mm is None
        assert cfg.module_mm is None
        assert cfg.use_calibration is True

    def test_round_trip_persists_quality_enum(self, store):
        cfg = PrinterPrintConfig("Q", quality=PrintQuality.DRAFT, dpi=300)
        store.save(cfg)
        data = json.loads(store._path("Q").read_text(encoding="utf-8"))
        assert data["quality"] == "draft"
        loaded = store.load("Q")
        assert loaded.quality == PrintQuality.DRAFT
