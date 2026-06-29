from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import psutil

AUTO_INTERFACE = "auto"


@dataclass(frozen=True)
class NetworkInterface:
    name: str
    kind: str
    label: str
    is_up: bool
    ipv4: str = ""

    @property
    def display(self) -> str:
        ip = f" {self.ipv4}" if self.ipv4 else ""
        inactive = " (inativa)" if not self.is_up else ""
        return f"{self.label}{ip}{inactive}"

    @property
    def display_full(self) -> str:
        state = "ativa" if self.is_up else "inativa"
        ip = f" ({self.ipv4})" if self.ipv4 else ""
        return f"{self.label} — {self.name}{ip} [{state}]"


def _guess_kind(name: str) -> str:
    lower = name.lower()
    if lower.startswith(("wl", "wifi", "wlan")):
        return "wifi"
    if lower.startswith(("en", "eth", "eno", "ens", "enp")):
        return "ethernet"
    if lower.startswith(("br-", "docker", "veth", "virbr")):
        return "virtual"
    return "other"


def _kind_label(kind: str) -> str:
    return {
        "wifi": "Wi-Fi",
        "ethernet": "Cabo",
        "virtual": "Virtual",
        "other": "Outra",
    }.get(kind, kind.title())


def _read_sysfs_operstate(name: str) -> str:
    path = Path(f"/sys/class/net/{name}/operstate")
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return "unknown"


def list_interfaces(include_virtual: bool = False) -> list[NetworkInterface]:
    stats = psutil.net_if_stats()
    addrs = psutil.net_if_addrs()
    result: list[NetworkInterface] = []

    for name, iface_stats in stats.items():
        if name == "lo":
            continue
        kind = _guess_kind(name)
        if not include_virtual and kind == "virtual":
            continue

        ipv4 = ""
        for addr in addrs.get(name, []):
            if addr.family.name == "AF_INET":
                ipv4 = addr.address
                break

        operstate = _read_sysfs_operstate(name)
        is_up = iface_stats.isup and operstate in ("up", "unknown")

        result.append(
            NetworkInterface(
                name=name,
                kind=kind,
                label=_kind_label(kind),
                is_up=is_up,
                ipv4=ipv4,
            )
        )

    order = {"ethernet": 0, "wifi": 1, "other": 2, "virtual": 3}
    result.sort(key=lambda i: (order.get(i.kind, 9), not i.is_up, i.name))
    return result


def interface_choices(include_virtual: bool = False) -> list[tuple[str, str]]:
    choices = [(AUTO_INTERFACE, "Automática")]
    for iface in list_interfaces(include_virtual=include_virtual):
        choices.append((iface.name, iface.display))
    return choices


def interface_tooltip(name: str) -> str:
    if name == AUTO_INTERFACE:
        return "Usa a interface padrão do sistema"
    for iface in list_interfaces(include_virtual=True):
        if iface.name == name:
            return iface.display_full
    return name


def resolve_interface_label(name: str) -> str:
    if name == AUTO_INTERFACE:
        return "Automática"
    for iface in list_interfaces(include_virtual=True):
        if iface.name == name:
            return f"{iface.label} ({iface.name})"
    return name


def ip_to_interface(ip: str) -> str | None:
    if not ip or ip.startswith("127."):
        return None
    for name, addrs in psutil.net_if_addrs().items():
        for addr in addrs:
            if addr.family.name == "AF_INET" and addr.address == ip:
                return name
    return None


def detect_process_interface(pid: int) -> str | None:
    try:
        proc = psutil.Process(pid)
        conns = proc.net_connections(kind="inet")
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
        return None

    for conn in conns:
        if not conn.laddr:
            continue
        local_ip = conn.laddr.ip
        if local_ip in ("0.0.0.0", "::", "127.0.0.1", "::1"):
            continue
        iface = ip_to_interface(local_ip)
        if iface:
            return iface
    return None
