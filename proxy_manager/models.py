from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from proxy_manager.network import AUTO_INTERFACE


ProxyScheme = Literal["http", "https", "socks5"]
ProxySource = Literal["custom", "free", "paid", "tor", "direct"]
AutoProxyMode = Literal["fast", "tor", "auto"]

LOCAL_HOST = "127.0.0.1"
LOCAL_PORT = 7890


@dataclass
class ProxySettings:
    enabled: bool = False
    source: ProxySource = "free"
    scheme: ProxyScheme = "http"
    upstream_host: str = ""
    upstream_port: int = 8080
    username: str = ""
    password: str = ""
    paid_provider: str = "smartproxy"
    auto_proxy_mode: AutoProxyMode = "fast"
    target_country: str = ""
    no_proxy: str = "localhost,127.0.0.1,::1"
    extra_ca_certs: str = ""
    # Interface de rede para saída do proxy (AUTO_INTERFACE = usa rota padrão do OS)
    network_interface: str = AUTO_INTERFACE

    # legado — migrado para upstream_*
    host: str = ""
    port: int = LOCAL_PORT

    @property
    def local_url(self) -> str:
        return f"http://{LOCAL_HOST}:{LOCAL_PORT}"

    @property
    def url(self) -> str:
        return self.local_url

    @property
    def display_url(self) -> str:
        if self.source == "direct":
            return f"local {LOCAL_HOST}:{LOCAL_PORT} → internet direta (sem Tor)"
        if self.upstream_host:
            return f"local {LOCAL_HOST}:{LOCAL_PORT} → {self.upstream_scheme_display}"
        return f"local {LOCAL_HOST}:{LOCAL_PORT} (configure proxy externo)"

    @property
    def upstream_scheme_display(self) -> str:
        auth = f"{self.username}:***@" if self.username else ""
        return f"{self.scheme}://{auth}{self.upstream_host}:{self.upstream_port}"


@dataclass
class AppRule:
    id: str
    name: str
    patterns: list[str]
    use_proxy: bool = True
    enabled: bool = True
    command: str = ""
    category: str = "custom"
    notes: str = ""
    network_interface: str = AUTO_INTERFACE
    upstream_proxy: str = ""
    use_socks5: bool = False

    def matches_process(self, name: str, cmdline: str) -> bool:
        haystack = f"{name} {cmdline}".lower()
        return any(p.lower() in haystack for p in self.patterns)


@dataclass
class ProcessInfo:
    pid: int
    name: str
    cmdline: str
    matched_app: AppRule | None
    proxy_env: dict[str, str] = field(default_factory=dict)
    proxy_active: bool = False
    status: str = "unknown"
    network_interface: str | None = None
