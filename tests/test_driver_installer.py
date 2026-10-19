"""driver_installer 纯逻辑测试（pytest 风格，仓库根目录运行 ``pytest -q``）。

覆盖：
- detect_driver_state 全组合矩阵（队列/离线/失败标记/驱动名/空名）
- build_driver_guidance 命中与未命中（未命中 official_url 必须为 None）
- elevate_process 在 Linux 抛 PrintError(UNSUPPORTED_PLATFORM)
- detect_driver_state_windows 在 Linux 抛 PrintError(UNSUPPORTED_PLATFORM)
- install_guidance_for_state 四类文案关键词断言
"""

import itertools

import pytest

from client.printing.driver_installer import (
    DriverState,
    build_driver_guidance,
    detect_driver_state,
    detect_driver_state_windows,
    elevate_process,
    install_guidance_for_state,
)
from client.printing.errors import PrintError


# ---------------------------------------------------------------------------
# detect_driver_state 全组合矩阵
# ---------------------------------------------------------------------------
def _expected(name: str, has_queue: bool, driver_name: str | None,
              is_offline: bool, install_failed: bool) -> DriverState:
    """与模块 docstring 规则表一致的参考实现，用于矩阵比对。"""
    if not name or not str(name).strip():
        return DriverState.UNKNOWN
    if not has_queue:
        return DriverState.QUEUE_MISSING
    if is_offline:
        return DriverState.DEVICE_OFFLINE
    if install_failed:
        return DriverState.DRIVER_INSTALL_FAILED
    if not driver_name or not str(driver_name).strip():
        return DriverState.DRIVER_MISSING
    return DriverState.DRIVER_OK


NAMES = ["", "  ", "Zebra ZD420"]
QUEUE = [False, True]
DRIVERS = [None, "", "ZDesigner ZD420"]
OFFLINE = [False, True]
FAILED = [False, True]


@pytest.mark.parametrize(
    "name,has_queue,driver_name,is_offline,install_failed",
    list(itertools.product(NAMES, QUEUE, DRIVERS, OFFLINE, FAILED)),
)
def test_state_matrix_all_combinations(name, has_queue, driver_name,
                                      is_offline, install_failed):
    result = detect_driver_state(name, has_queue, driver_name,
                                 is_offline, install_failed)
    assert result == _expected(name, has_queue, driver_name,
                               is_offline, install_failed)


def test_priority_queue_missing_beats_offline_and_failed():
    # 无队列优先于离线/失败标记
    assert detect_driver_state("p", False, "Drv", True, True) \
        is DriverState.QUEUE_MISSING


def test_priority_offline_beats_failed_and_driver_present():
    assert detect_driver_state("p", True, "Drv", True, True) \
        is DriverState.DEVICE_OFFLINE


def test_priority_failed_flag_beats_driver_present():
    assert detect_driver_state("p", True, "Drv", False, True) \
        is DriverState.DRIVER_INSTALL_FAILED


def test_missing_driver_when_queue_exists_but_driver_blank():
    for blank in (None, "", "   "):
        assert detect_driver_state("p", True, blank, False, False) \
            is DriverState.DRIVER_MISSING


def test_ok_when_all_healthy():
    assert detect_driver_state("p", True, "ZDesigner", False, False) \
        is DriverState.DRIVER_OK


def test_unknown_when_name_blank_even_if_queue_present():
    assert detect_driver_state("", True, "Drv", False, False) \
        is DriverState.UNKNOWN
    assert detect_driver_state("   ", True, None, True, True) \
        is DriverState.UNKNOWN


# ---------------------------------------------------------------------------
# build_driver_guidance 命中 / 未命中
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "manufacturer",
    ["Zebra", "zebra", "斑马", "TSC", "Godex", "科诚", "Brother",
     "Honeywell", "Star Micronics", "Epson", "SATO", "HPRT", "汉印",
     "Gprinter", "佳博", "Xprinter", "芯烨"],
)
def test_guidance_hit_returns_official_url(manufacturer):
    g = build_driver_guidance(manufacturer, "some-model")
    assert g.matched is True
    assert g.official_url and g.official_url.startswith("http")
    assert "官方" in g.notes or "驱动" in g.notes


def test_guidance_miss_url_is_none_and_no_fabrication():
    g = build_driver_guidance("FantasyBrand", "X-999")
    assert g.matched is False
    assert g.official_url is None
    assert g.generic_hint
    # 未命中不得给出任何猜测 URL
    assert "http" not in g.generic_hint


def test_guidance_blank_manufacturer_miss():
    g = build_driver_guidance("  ", "M1")
    assert g.matched is False
    assert g.official_url is None


# ---------------------------------------------------------------------------
# elevate_process / detect_driver_state_windows 在非 Windows 抛错
# ---------------------------------------------------------------------------
def test_elevate_process_raises_on_linux():
    with pytest.raises(PrintError) as exc:
        elevate_process("C:\\fake\\setup.exe", ["/install"])
    assert exc.value.code == PrintError.UNSUPPORTED_PLATFORM


def test_detect_windows_raises_on_linux():
    with pytest.raises(PrintError) as exc:
        detect_driver_state_windows("Zebra ZD420")
    assert exc.value.code == PrintError.UNSUPPORTED_PLATFORM


# ---------------------------------------------------------------------------
# install_guidance_for_state 四文案关键词
# ---------------------------------------------------------------------------
def test_guidance_ok_keywords():
    text = install_guidance_for_state(DriverState.DRIVER_OK)
    for kw in ("驱动就绪", "测试页", "校准"):
        assert kw in text


def test_guidance_missing_keywords():
    text = install_guidance_for_state(DriverState.DRIVER_MISSING)
    for kw in ("官方", "下载", "管理员", "UAC"):
        assert kw in text


def test_guidance_failed_keywords():
    text = install_guidance_for_state(DriverState.DRIVER_INSTALL_FAILED)
    for kw in ("卸载", "重启", "官方", "打印队列"):
        assert kw in text


def test_guidance_offline_keywords():
    text = install_guidance_for_state(DriverState.DEVICE_OFFLINE)
    for kw in ("电源", "线缆", "暂停", "疑难解答"):
        assert kw in text


def test_guidance_covers_remaining_states():
    for state in (DriverState.QUEUE_MISSING, DriverState.UNKNOWN):
        assert install_guidance_for_state(state)
