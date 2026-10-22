"""第四轮 P1「打印链路检查」综合守卫（grep/import 级，不重写打印代码）。

本文件把打印链路六条诚信红线固化为源码扫描 + 轻量导入级断言；
行为级语义由既有测试承担（test_print_pdf_semantics / test_print_config_semantics
/ test_scale_risk / test_adapters_content / test_transport_honesty /
test_protocol_state_audit），本文件不重复其参数化用例，只做"链路未被改坏"
的回归门禁。

六条红线：
1. 模板份数唯一展开：render_label_sheet_pdf 签名无 copies、只按 template.copies
   展开一次；print_export 生产调用点传原始 items，不预展开。
2. 静区/模块宽/偏移/纸张/DPI 进入最终输出：units 换算、pdf meta 回执、
   协议指令（^LH/^BY/GAP/REFERENCE）、Qt setResolution/setCopyCount/原子页布局。
3. 通用 Windows 队列不保证第三方驱动物理缩放：scale_policy warn 诚信文案，
   UI 明确"PDF 页面正确≠物理输出正确"。
4. 未实现通道（USB/蓝牙/LPR）继续明确抛 TRANSPORT_NOT_IMPLEMENTED。
5. 网络发送/队列提交 ≠ 物理打印完成：queued/sent 文案只说"不代表物理打印完成"。
6. 未实测型号保持未验证：client/printing 源码不得出现 verified_with_hardware=True。
"""

from __future__ import annotations

import inspect
import pathlib
import re

import pytest

from client.printing.adapters.transports import (
    BluetoothChannel, UsbChannel, parse_network_endpoint,
)
from client.printing.errors import PrintError
from client.printing.job import JobOutcome, PrintResult
from client.printing.pdf import render_label_sheet_pdf

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
_PRINTING = _REPO_ROOT / "client" / "printing"
_PDF = _PRINTING / "pdf.py"
_QT_PRINT = _PRINTING / "qt_print.py"
_SCALE = _PRINTING / "scale_policy.py"
_UNITS = _PRINTING / "units.py"
_TRANSPORTS = _PRINTING / "adapters" / "transports.py"
_ZPL = _PRINTING / "adapters" / "zpl.py"
_TSPL = _PRINTING / "adapters" / "tspl.py"
_PRINT_EXPORT = _REPO_ROOT / "client" / "ui" / "pages" / "print_export.py"


def _read(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 红线 1：模板份数唯一展开，任务份数不进 PDF，调用方不预展开
# ---------------------------------------------------------------------------

def test_render_signature_has_no_copies_param():
    """渲染函数签名层面不接受 copies（任务份数无法经签名进入 PDF）。"""
    params = inspect.signature(render_label_sheet_pdf).parameters
    assert "copies" not in params


def test_pdf_expands_template_copies_exactly_once():
    """pdf.py 只在内部按 template.copies 展开一次，无第二处 items 复制放大。"""
    src = _read(_PDF)
    # 唯一展开循环：for _ in range(copies) 把 item 追加进 expanded
    assert re.search(r"for _ in range\(copies\):", src), "缺少模板份数唯一展开循环"
    # 禁止在渲染器内再把 items 整体乘法复制（如 items * copies / [x for _ in ...]×2）
    loops = re.findall(r"for _ in range\(copies\)", src)
    assert len(loops) == 1, f"模板份数展开循环应仅一处，实际 {len(loops)} 处"


def test_print_export_passes_raw_items_not_pre_expanded():
    """生产调用点（导出 PDF / 通用队列）把原始 items 传给渲染器，
    预展开仅用于屏幕预览（preview.set_data），不得喂给渲染器。"""
    src = _read(_PRINT_EXPORT)
    # 渲染调用不得接受已展开的 expanded 列表
    assert "render_label_sheet_pdf(expanded" not in src
    # 唯一的列表预展开应只服务于预览
    preview_expand = "[it for it in items for _ in range(t.copies)]"
    assert preview_expand in src, "缺少预览专用预展开（预期仅服务 preview.set_data）"
    # 两处渲染调用均以原始 items 为首参数（允许跨行，断言调用块起始）
    calls = re.findall(
        r"render_label_sheet_pdf\(\s*\n?\s*items\b", src)
    assert len(calls) >= 2, "应存在导出/打印两处以原始 items 调用渲染器"


# ---------------------------------------------------------------------------
# 红线 2：静区/模块/偏移/纸张/DPI 进入最终输出
# ---------------------------------------------------------------------------

def test_pdf_meta_reports_applied_quiet_module_offset_paper():
    """pdf 返回 meta 必须回执静区模块、模块宽、偏移、纸张尺寸等真实生效值。"""
    src = _read(_PDF)
    for key in (
        "quiet_modules_applied", "quiet_zone_mm_applied",
        "module_mm_applied", "offset_x_mm", "offset_y_mm",
        "paper_width_mm", "paper_height_mm",
    ):
        assert f'"{key}"' in src or f"'{key}'" in src, f"pdf meta 缺少回执 {key}"


def test_units_provide_mm_dots_and_quiet_conversion():
    """units 提供毫米↔点/DPI 与静区模块换算，物理量不被当作相对量。"""
    src = _read(_UNITS)
    assert "def mm_to_dots" in src
    assert "def quiet_zone_mm_to_modules" in src


def test_protocol_instructions_carry_offset_module_gap():
    """专用协议：ZPL 经 ^LH 偏移 / ^BY 模块宽；TSPL 经 REFERENCE 偏移 / GAP 间隙。"""
    zpl = _read(_ZPL)
    tspl = _read(_TSPL)
    assert "^LH" in zpl, "ZPL 缺少 ^LH 原点偏移指令"
    assert "^BY" in zpl, "ZPL 缺少 ^BY 模块宽度指令"
    assert "REFERENCE" in tspl, "TSPL 缺少 REFERENCE 偏移指令"
    assert "GAP" in tspl, "TSPL 缺少 GAP 间隙指令"


def test_qt_print_atomic_layout_resolution_copycount():
    """Qt 通用队列：原子页布局(ExactMatch) + setResolution + 唯一 setCopyCount。"""
    src = _read(_QT_PRINT)
    assert "QPageSize.ExactMatch" in src
    assert "setPageLayout" in src, "应使用单个 setPageLayout 原子下发页面+方向"
    assert "setResolution" in src, "缺少 setResolution(DPI)"
    assert "setCopyCount" in src, "任务份数唯一应用点 setCopyCount 缺失"
    # setCopyCount 实际代码调用只出现一次（唯一应用点；docstring 提及不计）
    assert src.count("setCopyCount(max(1, int(spec.copies)))") == 1


# ---------------------------------------------------------------------------
# 红线 3：通用队列不保证第三方驱动物理缩放（warn 诚信文案 + UI 提示）
# ---------------------------------------------------------------------------

def test_scale_policy_honesty_reminder_present():
    """scale_policy 固定诚信措辞：页面正确 ≠ 物理输出正确。"""
    from client.printing import scale_policy
    assert "PDF 页面尺寸正确不代表物理标签输出尺寸正确" \
        in scale_policy.HONESTY_REMINDER
    assert "第三方驱动缩放不受本软件控制" in scale_policy.HONESTY_REMINDER


def test_ui_scale_dialog_states_pdf_correct_not_physical():
    """UI 缩放确认对话框向用户明示 PDF 页面正确不代表物理输出正确。"""
    src = _read(_PRINT_EXPORT)
    assert "PDF 页面尺寸正确不代表物理标签输出尺寸正确" in src
    assert "第三方驱动缩放不受本软件控制" in src


# ---------------------------------------------------------------------------
# 红线 4：未实现通道 USB / 蓝牙 / LPR 明确报错
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("channel", [UsbChannel(""), BluetoothChannel("")])
def test_usb_bluetooth_open_raises_not_implemented(channel):
    with pytest.raises(PrintError) as ei:
        channel.open()
    assert ei.value.code == PrintError.TRANSPORT_NOT_IMPLEMENTED


@pytest.mark.parametrize("channel", [UsbChannel(""), BluetoothChannel("")])
def test_usb_bluetooth_write_raises_not_implemented(channel):
    with pytest.raises(PrintError) as ei:
        channel.write(b"x")
    assert ei.value.code == PrintError.TRANSPORT_NOT_IMPLEMENTED


def test_lpr_endpoint_rejected_not_implemented():
    with pytest.raises(PrintError) as ei:
        parse_network_endpoint("lpr://192.168.1.20:515")
    assert ei.value.code == PrintError.TRANSPORT_NOT_IMPLEMENTED


def test_transports_source_marks_usb_bt_lpr_unimplemented():
    src = _read(_TRANSPORTS)
    assert "class UsbChannel" in src
    assert "class BluetoothChannel" in src
    assert "lpr://" in src and "TRANSPORT_NOT_IMPLEMENTED" in src


# ---------------------------------------------------------------------------
# 红线 5：队列提交 / 网络发送 ≠ 物理打印完成
# ---------------------------------------------------------------------------

def test_queued_message_does_not_claim_physical_print():
    r = PrintResult.queued("打印机A", job_id=7)
    assert r.outcome is JobOutcome.QUEUED
    assert r.outcome is not JobOutcome.PHYSICALLY_PRINTED
    assert "不代表物理打印完成" in r.message
    assert "已物理打印" not in r.message


def test_sent_message_does_not_claim_physical_print():
    r = PrintResult.sent("192.168.1.20:9100")
    assert r.outcome is JobOutcome.SENT
    assert r.outcome is not JobOutcome.PHYSICALLY_PRINTED
    assert "不代表物理打印完成" in r.message


def test_no_channel_yields_physically_printed_by_default():
    """当前任何提交路径都不得直接给出 PHYSICALLY_PRINTED（无设备确认通道）。"""
    src = _read(_PRINTING / "job.py")
    # PHYSICALLY_PRINTED 仅作为枚举常量存在，queued/sent 工厂不得返回它
    assert "JobOutcome.PHYSICALLY_PRINTED" in src
    queued_block = src.split("def queued")[1].split("def sent")[0]
    sent_block = src.split("def sent")[1].split("def failed")[0]
    assert "PHYSICALLY_PRINTED" not in queued_block
    assert "PHYSICALLY_PRINTED" not in sent_block


# ---------------------------------------------------------------------------
# 红线 6：未实测型号保持未验证（client/printing 源码无 verified=True 字面量）
# ---------------------------------------------------------------------------

def test_printing_source_has_no_verified_true_assignment():
    pat = re.compile(r"verified_with_hardware\s*(?<![=<>!])=(?![=])\s*True\b")
    offenders: list[str] = []
    for py in _PRINTING.rglob("*.py"):
        for lineno, line in enumerate(py.read_text(encoding="utf-8").splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue
            if pat.search(line):
                offenders.append(f"{py.relative_to(_REPO_ROOT)}:{lineno}")
    assert not offenders, (
        "printing 源码不得把 verified_with_hardware 赋为 True："
        + "; ".join(offenders))
