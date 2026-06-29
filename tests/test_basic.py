from __future__ import annotations

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
