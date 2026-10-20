"""适配器注册表：按 protocol 分发，提供能力/未实测标识查询。

- :func:`build_adapter`：按 ``capabilities.protocol`` 构造适配器；
  未知协议抛 ``PrintError(PROTOCOL_UNSUPPORTED)``。
- :func:`protocol_supported`：某协议是否已注册（供 UI 过滤）。
- :func:`verified`：透传 ``capabilities.verified_with_hardware``，
  供 UI 展示"未实测"标识；严禁在此伪造 True。

本模块不在导入期触碰 A 切片（windows_print/qt_print/pdf_render 均惰性），
可在 Linux 直接导入。
"""

from __future__ import annotations

from ..capabilities import Protocol, PrinterCapabilities
from ..errors import PrintError
from .base import BasePrinterAdapter
from .cpcl import CPCLAdapter
from .tspl import TSPLAdapter
from .universal import UniversalWindowsAdapter
from .zpl import ZPLAdapter

_REGISTRY: dict[Protocol, type[BasePrinterAdapter]] = {
    Protocol.UNIVERSAL: UniversalWindowsAdapter,
    Protocol.ZPL: ZPLAdapter,
    Protocol.TSPL: TSPLAdapter,
    Protocol.CPCL: CPCLAdapter,
}


def build_adapter(capabilities: PrinterCapabilities) -> BasePrinterAdapter:
    """按协议构造适配器；未知协议抛 ``PROTOCOL_UNSUPPORTED``。

    兼容 ``protocol`` 被赋成原始字符串的场景（防御性归一化）。
    """
    proto = capabilities.protocol
    if not isinstance(proto, Protocol):
        try:
            proto = Protocol(proto)
        except (ValueError, TypeError):
            proto = None
    cls = _REGISTRY.get(proto) if proto is not None else None
    if cls is None:
        raise PrintError(
            PrintError.PROTOCOL_UNSUPPORTED,
            f"不支持的打印协议: {getattr(capabilities.protocol, 'value', capabilities.protocol)}",
            resolution="支持 universal / zpl / tspl / cpcl",
        )
    return cls(capabilities)


def protocol_selection_basis(capabilities: PrinterCapabilities) -> str:
    """返回协议选择依据的人读描述（纯函数，不做任何品牌推断）。

    红线：协议**只能**由用户显式选择或能力模型显式声明
    （``capabilities.protocol``），**严禁根据厂商名 / 驱动名 / 端口名
    自动断言品牌→协议映射**（例如见到 "Zebra" 就认定 ZPL、见到
    "TSC" 就认定 TSPL）。本函数不读取 driver_name / printer_name 做
    匹配，只如实说明当前声明来源与实测状态。
    """
    verified = "已经真实设备实测" if capabilities.verified_with_hardware \
        else "未经真实设备实测"
    proto = getattr(capabilities.protocol, "value", capabilities.protocol)
    return (
        f"协议 {proto} 由用户显式选择或设备能力模型显式声明，"
        "未按厂商名/驱动名推断；" + verified
    )


def protocol_supported(protocol: Protocol | str) -> bool:
    """协议是否已注册支持（接受 Protocol 枚举或字符串）。"""
    if isinstance(protocol, str):
        try:
            protocol = Protocol(protocol)
        except ValueError:
            return False
    return protocol in _REGISTRY


def verified(adapter: BasePrinterAdapter) -> bool:
    """透传设备是否经真实硬件实测（UI"未实测"标识用）。

    直接返回 ``adapter.verified``（即 ``capabilities.verified_with_hardware``），
    未实测一律 False。
    """
    return bool(adapter.verified)
