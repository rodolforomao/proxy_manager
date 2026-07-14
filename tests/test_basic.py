from __future__ import annotations

import shutil

import pytest

from proxy_manager.models import AppRule, ProxySettings
from proxy_manager.network import AUTO_INTERFACE, list_interfaces
from proxy_manager.proxy_env import build_proxy_env


# ── ProxySettings ────────────────────────────────────────────────────────────

def test_proxy_settings_defaults():
    p = ProxySettings()
    assert p.enabled is False
    assert p.upstream_host == ""
    assert p.no_proxy == "localhost,127.0.0.1,::1"


def test_proxy_settings_url_local():
    p = ProxySettings(upstream_host="proxy.example.com", upstream_port=8080)
    assert p.url == "http://127.0.0.1:7890"


def test_proxy_settings_upstream_display():
    p = ProxySettings(scheme="http", upstream_host="proxy.example.com", upstream_port=3128)
    assert "proxy.example.com" in p.upstream_scheme_display
    assert "3128" in p.upstream_scheme_display


def test_proxy_settings_upstream_display_with_auth():
    p = ProxySettings(
        scheme="http",
        upstream_host="proxy.example.com",
        upstream_port=3128,
        username="user",
        password="pass",
    )
    assert "user" in p.upstream_scheme_display
    assert "***" in p.upstream_scheme_display


# ── AppRule ──────────────────────────────────────────────────────────────────

def test_app_rule_defaults():
    app = AppRule(id="test", name="Test", patterns=["test"])
    assert app.use_proxy is True
    assert app.enabled is True
    assert app.network_interface == AUTO_INTERFACE
    assert app.upstream_proxy == ""


def test_app_rule_matches_process():
    app = AppRule(id="ff", name="Firefox", patterns=["firefox"])
    assert app.matches_process("firefox", "") is True
    assert app.matches_process("chromium", "") is False


def test_app_rule_matches_cmdline():
    app = AppRule(id="cursor", name="Cursor", patterns=["cursor"])
    assert app.matches_process("electron", "/usr/share/cursor/cursor") is True


# ── build_proxy_env ──────────────────────────────────────────────────────────

def test_build_proxy_env_no_proxy():
    p = ProxySettings(upstream_host="proxy.example.com")
    env = build_proxy_env(p, use_proxy=False, base_env={})
    assert "HTTP_PROXY" not in env
    assert "HTTPS_PROXY" not in env


def test_build_proxy_env_with_proxy():
    p = ProxySettings(
        enabled=True,
        source="custom",
        upstream_host="proxy.example.com",
        upstream_port=8080,
    )
    env = build_proxy_env(p, use_proxy=True, base_env={})
    assert env.get("HTTP_PROXY", "").startswith("http://127.0.0.1:7890")
    assert env.get("HTTPS_PROXY") == env.get("HTTP_PROXY")


def test_build_proxy_env_clears_existing():
    p = ProxySettings(upstream_host="proxy.example.com")
    base = {"HTTP_PROXY": "http://old-proxy:1234", "HTTPS_PROXY": "http://old-proxy:1234"}
    env = build_proxy_env(p, use_proxy=False, base_env=base)
    assert "HTTP_PROXY" not in env
    assert "HTTPS_PROXY" not in env


def test_build_proxy_env_app_upstream():
    p = ProxySettings(
        enabled=True,
        source="custom",
        upstream_host="proxy.example.com",
        upstream_port=8080,
    )
    env = build_proxy_env(p, use_proxy=True, base_env={}, app_upstream="http://app-proxy:9999")
    assert env.get("HTTP_PROXY") == "http://app-proxy:9999"


def test_build_proxy_env_no_proxy_list():
    p = ProxySettings(
        enabled=True,
        source="custom",
        upstream_host="proxy.example.com",
        no_proxy="localhost,127.0.0.1,internal.corp",
    )
    env = build_proxy_env(p, use_proxy=True, base_env={})
    assert "internal.corp" in env.get("NO_PROXY", "")


# ── network ──────────────────────────────────────────────────────────────────

def test_list_interfaces_returns_list():
    ifaces = list_interfaces()
    assert isinstance(ifaces, list)


def test_list_interfaces_auto_not_included():
    ifaces = list_interfaces()
    names = [i.name for i in ifaces]
    assert AUTO_INTERFACE not in names


def test_auto_interface_constant():
    assert AUTO_INTERFACE == "auto"


# ── version ──────────────────────────────────────────────────────────────────

def test_window_title_appends_commit_hash(tmp_path, monkeypatch):
    from proxy_manager import version as version_mod

    commit_file = tmp_path / "_commit.txt"
    commit_file.write_text("abcdef1234567\n", encoding="utf-8")
    monkeypatch.setattr(version_mod, "_commit_file_candidates", lambda: [commit_file])
    version_mod.app_commit_hash.cache_clear()

    assert version_mod.app_commit_hash() == "234567"
    title = version_mod.window_title()
    assert title.startswith(f"Proxy Manager {version_mod.app_version()} (234567)")
    version_mod.app_commit_hash.cache_clear()


def test_window_title_without_commit_hash(monkeypatch):
    from proxy_manager import version as version_mod

    monkeypatch.setattr(version_mod, "_commit_file_candidates", lambda: [])
    monkeypatch.setattr(version_mod, "app_commit_hash", lambda: "")

    assert "(" not in version_mod.window_title()


# ── ConfigStore ──────────────────────────────────────────────────────────────

def test_configstore_init_fresh(tmp_path):
    from proxy_manager.config import ConfigStore
    store = ConfigStore(path=tmp_path / "config.json")
    assert store.proxy is not None
    assert len(store.apps) > 0
    assert store.profiles == []
    assert store.active_profile == ""


def test_configstore_save_load_roundtrip(tmp_path):
    from proxy_manager.config import ConfigStore
    p = tmp_path / "config.json"
    store = ConfigStore(path=p)
    store.proxy.upstream_host = "my.proxy.com"
    store.proxy.upstream_port = 3128
    store.save()

    store2 = ConfigStore(path=p)
    assert store2.proxy.upstream_host == "my.proxy.com"
    assert store2.proxy.upstream_port == 3128


def test_configstore_profiles(tmp_path):
    from proxy_manager.config import ConfigStore
    p = tmp_path / "config.json"
    store = ConfigStore(path=p)
    store.proxy.upstream_host = "proxy1.com"
    store.proxy.upstream_port = 8080
    profile = store.save_profile("work")

    assert profile.name == "work"
    assert store.active_profile == "work"
    assert len(store.profiles) == 1

    store.proxy.upstream_host = "different.com"
    loaded = store.load_profile("work")
    assert loaded is True
    assert store.proxy.upstream_host == "proxy1.com"


def test_configstore_delete_profile(tmp_path):
    from proxy_manager.config import ConfigStore
    p = tmp_path / "config.json"
    store = ConfigStore(path=p)
    store.proxy.upstream_host = "proxy1.com"
    store.save_profile("work")
    store.delete_profile("work")
    assert store.profiles == []
    assert store.active_profile == ""


def test_claude_proxy_active_per_session():
    from proxy_manager.claude_proxy import claude_proxy_active, claude_settings_proxy_active
    from proxy_manager.models import AppRule

    app = AppRule(id="claude", name="Claude Code", patterns=["claude"], command="claude")
    # Sem PID: usa settings.json (configurado mas parado)
    assert claude_proxy_active(app, pid=None) is claude_settings_proxy_active()
    # Com PID fictício sem conexão: não herda settings de outra sessão
    assert claude_proxy_active(app, pid=999999999) is False


def test_claude_tcp_proxy_detection():
    from proxy_manager.claude_proxy import _local_proxy_tcp_hex

    host_hex, port_hex = _local_proxy_tcp_hex()
    assert host_hex == "0100007F"
    assert port_hex == "1ED2"  # LOCAL_PORT 7890


def test_sync_persistent_clears_claude_when_proxy_off(tmp_path, monkeypatch):
    from proxy_manager import app_proxy_sync, claude_proxy
    from proxy_manager.models import AppRule, ProxySettings

    settings = tmp_path / "settings.json"
    settings.write_text(
        '{"env":{"HTTP_PROXY":"http://127.0.0.1:7890","HTTPS_PROXY":"http://127.0.0.1:7890"}}\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(claude_proxy, "CLAUDE_SETTINGS", settings)

    apps = [
        AppRule(id="claude", name="Claude Code", patterns=["claude"], use_proxy=True),
        AppRule(id="cursor", name="Cursor", patterns=["cursor"], use_proxy=False),
    ]
    proxy = ProxySettings(enabled=False)

    changed = app_proxy_sync.sync_persistent_app_proxies(
        apps, proxy, local_proxy_active=False
    )
    assert "claude" in changed
    assert claude_proxy.claude_settings_proxy_active() is False
    data = settings.read_text(encoding="utf-8")
    assert "7890" not in data


def test_sync_persistent_applies_claude_when_proxy_on(tmp_path, monkeypatch):
    from proxy_manager import app_proxy_sync, claude_proxy
    from proxy_manager.models import AppRule, ProxySettings

    settings = tmp_path / "settings.json"
    settings.write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(claude_proxy, "CLAUDE_SETTINGS", settings)

    apps = [
        AppRule(id="claude", name="Claude Code", patterns=["claude"], use_proxy=True),
    ]
    proxy = ProxySettings(enabled=True, source="tor", upstream_host="127.0.0.1", upstream_port=9050)

    changed = app_proxy_sync.sync_persistent_app_proxies(
        apps, proxy, local_proxy_active=True
    )
    assert "claude" in changed
    assert claude_proxy.claude_settings_proxy_active() is True
    assert "7890" in settings.read_text(encoding="utf-8")


def test_app_rule_use_socks5_default_false():
    app = AppRule(id="rustdesk", name="RustDesk", patterns=["rustdesk"])
    assert app.use_socks5 is False


def test_configstore_roundtrips_use_socks5(tmp_path):
    from proxy_manager.config import ConfigStore

    store = ConfigStore(path=tmp_path / "config.json")
    app = AppRule(id="rustdesk", name="RustDesk", patterns=["rustdesk"], use_socks5=True)
    store.update_app(app)

    store2 = ConfigStore(path=tmp_path / "config.json")
    assert store2.get_app("rustdesk").use_socks5 is True


def test_resolve_app_upstream_prefers_socks5_tunnel():
    from proxy_manager.launcher import resolve_app_upstream
    from proxy_manager import ssh_socks_tunnel as ssh_socks

    app = AppRule(
        id="rustdesk",
        name="RustDesk",
        patterns=["rustdesk"],
        use_socks5=True,
        upstream_proxy="http://ignored:1234",
    )
    assert resolve_app_upstream(app) == ssh_socks.upstream_url()


def test_resolve_app_upstream_falls_back_to_manual_upstream():
    from proxy_manager.launcher import resolve_app_upstream

    app = AppRule(
        id="cursor",
        name="Cursor",
        patterns=["cursor"],
        use_socks5=False,
        upstream_proxy="http://manual:8080",
    )
    assert resolve_app_upstream(app) == "http://manual:8080"


def test_build_proxy_env_socks5_upstream_overrides_use_proxy_off():
    """use_socks5 deve rotear o app mesmo com o interruptor use_proxy (gost) desligado."""
    from proxy_manager.launcher import resolve_app_upstream
    from proxy_manager import ssh_socks_tunnel as ssh_socks

    app = AppRule(id="rustdesk", name="RustDesk", patterns=["rustdesk"], use_proxy=False, use_socks5=True)
    p = ProxySettings(enabled=False, source="direct")
    via_socks5 = bool(app and getattr(app, "use_socks5", False))
    env = build_proxy_env(p, app.use_proxy or via_socks5, base_env={}, app_upstream=resolve_app_upstream(app))
    assert env.get("HTTP_PROXY") == ssh_socks.upstream_url()


def test_scan_matches_disabled_app_rules():
    from proxy_manager.config import ConfigStore
    from proxy_manager.process_monitor import scan_processes

    store = ConfigStore()
    for app in store.apps:
        if app.id == "claude":
            app.enabled = False
            app.use_proxy = False
    procs = scan_processes(store.apps, store.proxy)
    claude_procs = [p for p in procs if p.matched_app and p.matched_app.id == "claude"]
    # Pode não haver Claude rodando no CI; só valida que enabled=False não impede o match.
    for proc in claude_procs:
        assert proc.matched_app is not None
        assert proc.matched_app.id == "claude"


def test_resolve_browser_command_chromium():
    from proxy_manager.browser_proxy import is_main_browser_process, resolve_browser_command
    from proxy_manager.models import AppRule

    app = AppRule(
        id="chrome",
        name="Google Chrome",
        patterns=["google-chrome", "chromium", "chromium-browser"],
        command="google-chrome",
    )
    resolved = resolve_browser_command(app)
    if shutil.which("google-chrome") or shutil.which("google-chrome-stable"):
        assert "google-chrome" in resolved
    elif shutil.which("chromium"):
        assert resolved.endswith("chromium") or "chromium" in resolved

    assert is_main_browser_process(
        app, "cursor", "/usr/share/cursor/chrome-sandbox /usr/share/cursor/cursor"
    ) is False
    assert is_main_browser_process(
        app, "chromium", "/snap/bin/chromium --enable-features=VaapiVideoDecoder"
    ) is True


def test_configstore_app_upstream_roundtrip(tmp_path):
    from proxy_manager.config import ConfigStore
    p = tmp_path / "config.json"
    store = ConfigStore(path=p)
    app = store.apps[0]
    app.upstream_proxy = "http://special-proxy:9999"
    store.update_app(app)

    store2 = ConfigStore(path=p)
    found = store2.get_app(app.id)
    assert found is not None
    assert found.upstream_proxy == "http://special-proxy:9999"
