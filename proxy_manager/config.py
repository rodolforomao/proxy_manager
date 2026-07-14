from __future__ import annotations

import json
import os
from pathlib import Path

from proxy_manager.models import LOCAL_PORT, AppRule, ProxySettings
from proxy_manager.network import AUTO_INTERFACE
from proxy_manager.presets import default_apps
from proxy_manager.profiles import ProxyProfile

CONFIG_DIR = Path.home() / ".config" / "proxy-manager"
CONFIG_FILE = CONFIG_DIR / "config.json"
CONFIG_VERSION = 15
RECENT_APPS_MAX = 20


def _ensure_config_dir() -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)


def _app_to_dict(app: AppRule) -> dict:
    return {
        "id": app.id,
        "name": app.name,
        "patterns": app.patterns,
        "use_proxy": app.use_proxy,
        "enabled": app.enabled,
        "command": app.command,
        "category": app.category,
        "notes": app.notes,
        "network_interface": app.network_interface,
        "upstream_proxy": app.upstream_proxy,
        "use_socks5": app.use_socks5,
    }


def _app_from_dict(data: dict) -> AppRule:
    return AppRule(
        id=data["id"],
        name=data["name"],
        patterns=data.get("patterns", []),
        use_proxy=data.get("use_proxy", True),
        enabled=data.get("enabled", True),
        command=data.get("command", ""),
        category=data.get("category", "custom"),
        notes=data.get("notes", ""),
        network_interface=data.get("network_interface", AUTO_INTERFACE),
        upstream_proxy=data.get("upstream_proxy", ""),
        use_socks5=data.get("use_socks5", False),
    )


class ConfigStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or CONFIG_FILE
        self.proxy = ProxySettings()
        self.apps: list[AppRule] = []
        self.recent_app_ids: list[str] = []
        self.profiles: list[ProxyProfile] = []
        self.active_profile: str = ""
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            self.proxy = ProxySettings()
            self.apps = default_apps()
            self.save()
            return

        with open(self.path, encoding="utf-8") as f:
            data = json.load(f)

        proxy_data = data.get("proxy", {})
        legacy_host = proxy_data.get("host", "")
        legacy_port = int(proxy_data.get("port", LOCAL_PORT))
        upstream_host = proxy_data.get("upstream_host", "")
        upstream_port = int(proxy_data.get("upstream_port", legacy_port))
        if not upstream_host and legacy_host not in ("", "127.0.0.1", "localhost", "::1"):
            upstream_host = legacy_host
            upstream_port = legacy_port if legacy_port != LOCAL_PORT else 8080

        self.proxy = ProxySettings(
            enabled=proxy_data.get("enabled", False),
            source=proxy_data.get("source", "custom"),
            scheme=proxy_data.get("scheme", "http"),
            upstream_host=upstream_host,
            upstream_port=upstream_port,
            username=proxy_data.get("username", ""),
            password=proxy_data.get("password", ""),
            paid_provider=proxy_data.get("paid_provider", "smartproxy"),
            auto_proxy_mode=proxy_data.get("auto_proxy_mode", "fast"),  # type: ignore[arg-type]
            target_country=proxy_data.get("target_country", ""),
            no_proxy=proxy_data.get("no_proxy", "localhost,127.0.0.1,::1"),
            extra_ca_certs=proxy_data.get("extra_ca_certs", ""),
            network_interface=proxy_data.get("network_interface", AUTO_INTERFACE),
            host=legacy_host,
            port=legacy_port,
        )
        self.apps = [_app_from_dict(a) for a in data.get("apps", [])]
        if not self.apps:
            self.apps = default_apps()
        self.recent_app_ids: list[str] = list(data.get("recent_app_ids", []))
        self.profiles = [ProxyProfile.from_dict(p) for p in data.get("profiles", [])]
        self.active_profile = data.get("active_profile", "")

        if data.get("config_version", 1) < CONFIG_VERSION:
            self._migrate(data.get("config_version", 1))
            self.save()

    def _migrate(self, from_version: int) -> None:
        if from_version < 2:
            for app in self.apps:
                if app.id in ("cursor", "openai"):
                    app.use_proxy = False
        if from_version < 3:
            for app in self.apps:
                if app.id == "claude":
                    app.name = "Claude Code"
                    app.notes = "CLI — use Ligar proxy no app"
        if from_version < 4:
            for app in self.apps:
                app.use_proxy = False
        if from_version < 5:
            p = self.proxy
            if p.upstream_host in ("", "127.0.0.1", "localhost") and p.host not in (
                "",
                "127.0.0.1",
                "localhost",
            ):
                p.upstream_host = p.host
                p.upstream_port = p.port if p.port != LOCAL_PORT else 8080
        if from_version < 6:
            if self.proxy.upstream_host and self.proxy.source == "custom":
                pass  # mantém personalizado
        if from_version < 7:
            if not self.proxy.upstream_host.strip() and self.proxy.source == "custom":
                self.proxy.source = "free"
        if from_version < 8:
            pass  # auto_proxy_mode default fast — não altera upstream já configurado
        if from_version < 9:
            pass  # target_country default ""
        if from_version < 10:
            pass  # recent_app_ids default []
        if from_version < 11:
            pass  # upstream_proxy default ""
        if from_version < 12:
            pass  # network_interface default AUTO_INTERFACE
        if from_version < 13:
            for app in self.apps:
                if app.id == "chrome":
                    app.patterns = ["google-chrome", "chromium", "chromium-browser"]
                    app.notes = (
                        "Chrome ou Chromium — reinicia com --proxy-server apontando para o proxy local."
                    )
        if from_version < 14:
            if not any(a.id == "rustdesk" for a in self.apps):
                self.apps.append(
                    AppRule(
                        id="rustdesk",
                        name="RustDesk",
                        patterns=["rustdesk"],
                        use_proxy=False,
                        use_socks5=True,
                        category="tools",
                        command="rustdesk",
                        notes=(
                            "NÃO usa o proxy gost (:7890). Toggle S5 do card (ou o botão S5 "
                            "do header) liga o túnel SSH SOCKS5 em 127.0.0.1:1080 — configure "
                            "no RustDesk: Settings → Network → Socks5."
                        ),
                    )
                )
        if from_version < 15:
            for app in self.apps:
                if app.id == "rustdesk":
                    app.use_socks5 = True
                    app.notes = (
                        "NÃO usa o proxy gost (:7890). Toggle S5 do card (ou o botão S5 "
                        "do header) liga o túnel SSH SOCKS5 em 127.0.0.1:1080 — configure "
                        "no RustDesk: Settings → Network → Socks5."
                    )

    def touch_recent_app(self, app_id: str) -> None:
        recent = [aid for aid in self.recent_app_ids if aid != app_id]
        recent.insert(0, app_id)
        self.recent_app_ids = recent[:RECENT_APPS_MAX]
        self.save()

    def save(self) -> None:
        _ensure_config_dir()
        payload = {
            "config_version": CONFIG_VERSION,
            "proxy": {
                "enabled": self.proxy.enabled,
                "source": self.proxy.source,
                "scheme": self.proxy.scheme,
                "upstream_host": self.proxy.upstream_host,
                "upstream_port": self.proxy.upstream_port,
                "username": self.proxy.username,
                "password": self.proxy.password,
                "paid_provider": self.proxy.paid_provider,
                "auto_proxy_mode": self.proxy.auto_proxy_mode,
                "target_country": self.proxy.target_country,
                "no_proxy": self.proxy.no_proxy,
                "extra_ca_certs": self.proxy.extra_ca_certs,
                "network_interface": self.proxy.network_interface,
            },
            "apps": [_app_to_dict(a) for a in self.apps],
            "recent_app_ids": self.recent_app_ids,
            "profiles": [p.to_dict() for p in self.profiles],
            "active_profile": self.active_profile,
        }
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)

    def get_app(self, app_id: str) -> AppRule | None:
        for app in self.apps:
            if app.id == app_id:
                return app
        return None

    def add_app(self, app: AppRule) -> None:
        self.apps.append(app)
        self.save()

    def remove_app(self, app_id: str) -> None:
        self.apps = [a for a in self.apps if a.id != app_id]
        self.save()

    def update_app(self, app: AppRule) -> None:
        for i, existing in enumerate(self.apps):
            if existing.id == app.id:
                self.apps[i] = app
                self.save()
                return
        self.add_app(app)

    def reset_presets(self) -> None:
        self.apps = default_apps()
        self.save()

    def save_profile(self, name: str) -> ProxyProfile:
        """Salva configuração atual como perfil nomeado."""
        import copy
        profile = ProxyProfile.from_settings(name, copy.deepcopy(self.proxy))
        existing = next((i for i, p in enumerate(self.profiles) if p.name == name), None)
        if existing is not None:
            self.profiles[existing] = profile
        else:
            self.profiles.append(profile)
        self.active_profile = name
        self.save()
        return profile

    def load_profile(self, name: str) -> bool:
        """Carrega um perfil nomeado para a configuração ativa."""
        import copy
        profile = next((p for p in self.profiles if p.name == name), None)
        if profile is None:
            return False
        self.proxy = copy.deepcopy(profile.proxy)
        self.active_profile = name
        self.save()
        return True

    def delete_profile(self, name: str) -> None:
        self.profiles = [p for p in self.profiles if p.name != name]
        if self.active_profile == name:
            self.active_profile = ""
        self.save()
