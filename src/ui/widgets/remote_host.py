"""远程 J-Link 主机解析与可达性探测（RTT 页 / 烧录页共用）。"""
from __future__ import annotations

import ipaddress
import re
import socket

REMOTE_ITEM_TEXT = "远程连接…"

_HOSTNAME_RE = re.compile(
    r"^(?!-)[A-Za-z0-9-]{1,63}(?<!-)(\.(?!-)[A-Za-z0-9-]{1,63}(?<!-))*$"
)


def resolve_remote_host(host: str) -> str | None:
    """host 是 IPv4 字面量原样返回；是合法主机名（含 localhost）则解析为 IPv4 字符串。

    非法输入或解析失败返回 None。
    """
    host = host.strip()
    if not host:
        return None

    try:
        addr = ipaddress.ip_address(host)
        if isinstance(addr, ipaddress.IPv4Address):
            return host
        return None
    except ValueError:
        pass

    if len(host) > 253 or not _HOSTNAME_RE.match(host):
        return None

    try:
        infos = socket.getaddrinfo(host, None, family=socket.AF_INET)
        if not infos:
            return None
        return infos[0][4][0]
    except socket.gaierror:
        return None


def is_valid_port(text: str) -> bool:
    """1-65535 的十进制端口。"""
    if not text or not text.isdigit():
        return False
    try:
        port = int(text)
    except ValueError:
        return False
    return 1 <= port <= 65535


def tcp_reachable(ip: str, port: int, timeout: float = 2.0) -> bool:
    """socket.connect_ex 探测；True = 可达。"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        return sock.connect_ex((ip, port)) == 0
    finally:
        sock.close()
