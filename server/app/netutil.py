"""客户端地址解析：只信任明确配置的反向代理。

设计原则（对应审计问题 1）：

- 默认使用 TCP 连接的真实对端地址（`request.client.host`）；
- 只有连接对端落在 `CLS_TRUSTED_PROXIES` 白名单内时，才解析
  `X-Forwarded-For` / `X-Real-IP`；
- `X-Forwarded-For` 从左到右是"客户端, 代理1, 代理2..."，因此必须
  **从右往左**剥离可信代理，取第一个不可信地址——直接取最左会被
  客户端伪造的头部欺骗；
- 链中任意一项无法解析为合法 IP 时整体失败关闭，回退到连接对端，
  不猜测、不部分采纳。

本模块不依赖 FastAPI，便于单元测试。
"""

from __future__ import annotations

import ipaddress
from typing import Iterable, Sequence

UNKNOWN = "unknown"


def parse_networks(values: Iterable[str]) -> tuple[ipaddress._BaseNetwork, ...]:
    """解析 IP / CIDR 列表；非法项直接报错（不静默跳过）。"""
    nets = []
    for raw in values:
        text = (raw or "").strip()
        if not text:
            continue
        try:
            nets.append(ipaddress.ip_network(text, strict=False))
        except ValueError as exc:
            raise ValueError(f"非法的可信代理地址：{text}") from exc
    return tuple(nets)


def normalize_ip(raw: str | None) -> str | None:
    """规范化单个地址：去端口、去方括号、校验合法性。非法返回 None。"""
    if not raw:
        return None
    text = raw.strip().strip('"').strip()
    if not text:
        return None
    if text.startswith("["):          # [2001:db8::1]:443
        end = text.find("]")
        if end == -1:
            return None
        text = text[1:end]
    elif text.count(":") == 1:        # 192.0.2.1:5555
        host, _, port = text.partition(":")
        if port.isdigit():
            text = host
    try:
        return str(ipaddress.ip_address(text))
    except ValueError:
        return None


def is_trusted(ip: str, networks: Sequence[ipaddress._BaseNetwork]) -> bool:
    if not networks or not ip:
        return False
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return any(addr in net for net in networks)


def resolve_client_ip(
    peer: str | None,
    forwarded_for: str | None,
    real_ip: str | None,
    trusted: Sequence[ipaddress._BaseNetwork],
) -> str:
    """返回用于限流与审计的客户端地址。

    `peer` 必须是连接对端地址（FastAPI 的 request.client.host），
    绝不能是已解析过的转发头。
    """
    peer_ip = normalize_ip(peer) or UNKNOWN
    if peer_ip == UNKNOWN:
        return UNKNOWN
    # 对端不是可信代理：转发头一律不可信（这正是伪造头攻击面）
    if not is_trusted(peer_ip, trusted):
        return peer_ip

    chain_raw = [part for part in (forwarded_for or "").split(",") if part.strip()]
    if chain_raw:
        chain = [normalize_ip(part) for part in chain_raw]
        if all(item is not None for item in chain):
            for item in reversed(chain):
                if is_trusted(item, trusted):
                    continue
                return item
            # 整条链都是可信代理（配置异常），返回最左侧原始声明
            return chain[0]
        return peer_ip               # 含非法项：失败关闭

    real = normalize_ip(real_ip)
    if real:
        return real
    return peer_ip
