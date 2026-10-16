from proxy_manager.app_proxy_sync import clear_persistent_app_proxies
from proxy_manager.models import AppRule, ProxySettings
from proxy_manager.session_teardown import prepare_for_shutdown_is_stopping


def test_prepare_for_shutdown_true_only():
    line = (
        "/org/freedesktop/login1: "
        "org.freedesktop.login1.Manager.PrepareForShutdown (true)"
    )
    assert prepare_for_shutdown_is_stopping(line)
    assert not prepare_for_shutdown_is_stopping(
        "org.freedesktop.login1.Manager.PrepareForShutdown (false)"
    )
    assert not prepare_for_shutdown_is_stopping("PropertiesChanged")


def test_clear_persistent_strips_elements_conf(monkeypatch):
    calls: list[tuple[str, bool]] = []

    def _write(app_id: str, *, enable: bool, proxy_endpoint: str | None = None):
        calls.append((app_id, enable))
        return True, "ok"

    monkeypatch.setattr("proxy_manager.app_proxy_sync.write_conf_proxy", _write)
    monkeypatch.setattr(
        "proxy_manager.app_proxy_sync.prepare_claude_proxy",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        "proxy_manager.app_proxy_sync.prepare_browser_proxy",
        lambda *_a, **_k: None,
    )
    apps = [
        AppRule(id="liquid-testnet", name="Liquid", patterns=["elementsd"]),
        AppRule(id="firefox", name="Firefox", patterns=["firefox"]),
    ]
    changed = clear_persistent_app_proxies(apps, ProxySettings())
    assert ("liquid-testnet", False) in calls
    assert "liquid-testnet" in changed
    assert "firefox" in changed
