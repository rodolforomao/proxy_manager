from __future__ import annotations

from dataclasses import dataclass, field

from proxy_manager.models import ProxySettings


@dataclass
class ProxyProfile:
    name: str
    proxy: ProxySettings = field(default_factory=ProxySettings)

    def to_dict(self) -> dict:
        p = self.proxy
        return {
            "name": self.name,
            "proxy": {
                "enabled": p.enabled,
                "source": p.source,
                "scheme": p.scheme,
                "upstream_host": p.upstream_host,
                "upstream_port": p.upstream_port,
                "username": p.username,
                "password": p.password,
                "paid_provider": p.paid_provider,
                "auto_proxy_mode": p.auto_proxy_mode,
                "target_country": p.target_country,
                "no_proxy": p.no_proxy,
                "extra_ca_certs": p.extra_ca_certs,
            },
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ProxyProfile":
        pd = data.get("proxy", {})
        proxy = ProxySettings(
            enabled=pd.get("enabled", False),
            source=pd.get("source", "free"),
            scheme=pd.get("scheme", "http"),
            upstream_host=pd.get("upstream_host", ""),
            upstream_port=int(pd.get("upstream_port", 8080)),
            username=pd.get("username", ""),
            password=pd.get("password", ""),
            paid_provider=pd.get("paid_provider", "smartproxy"),
            auto_proxy_mode=pd.get("auto_proxy_mode", "fast"),
            target_country=pd.get("target_country", ""),
            no_proxy=pd.get("no_proxy", "localhost,127.0.0.1,::1"),
            extra_ca_certs=pd.get("extra_ca_certs", ""),
        )
        return cls(name=data.get("name", "Perfil"), proxy=proxy)

    @classmethod
    def from_settings(cls, name: str, settings: ProxySettings) -> "ProxyProfile":
        import copy
        return cls(name=name, proxy=copy.deepcopy(settings))
