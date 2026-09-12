from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from proxy_manager.models import AppRule
from proxy_manager import elements_proxy as ep


SAMPLE_CONF = """# Liquid Testnet
chain=liquidtestnet

txindex=1
server=1

[liquidtestnet]
rpcbind=0.0.0.0
rpcport=7039
"""


def test_elements_default_apps_ids():
    apps = ep.elements_default_apps()
    ids = {a.id for a in apps}
    assert ids == {"liquid-testnet", "liquid-mainnet", "liquid-mainnet-light"}
    assert all(a.category == "blockchain" for a in apps)
    assert all(a.command == "elements-tor" for a in apps)


def test_is_elements_app():
    assert ep.is_elements_app(AppRule(id="liquid-mainnet", name="X", patterns=[]))
    assert not ep.is_elements_app(AppRule(id="firefox", name="Firefox", patterns=["firefox"]))
    assert not ep.is_elements_app(None)


def test_write_conf_proxy_insert_and_remove(tmp_path, monkeypatch):
    conf_dir = tmp_path / "config"
    conf_dir.mkdir()
    conf = conf_dir / "elements.conf"
    conf.write_text(SAMPLE_CONF, encoding="utf-8")

    monkeypatch.setattr(ep, "LIQUID_CONFIG_DIR", conf_dir)
    monkeypatch.setattr(ep, "LIQUID_COMPOSE_DIR", tmp_path)

    ok, msg = ep.write_conf_proxy(
        "liquid-testnet", enable=True, proxy_endpoint="172.19.0.1:19050"
    )
    assert ok, msg
    text = conf.read_text(encoding="utf-8")
    assert ep._BEGIN in text
    assert "proxy=172.19.0.1:19050" in text
    assert "proxyrandomize=1" in text
    assert text.index(ep._BEGIN) < text.index("[liquidtestnet]")
    assert ep.conf_has_proxy("liquid-testnet") is True

    ok, msg = ep.write_conf_proxy("liquid-testnet", enable=False)
    assert ok, msg
    text = conf.read_text(encoding="utf-8")
    assert ep._BEGIN not in text
    assert "proxy=" not in text
    assert "[liquidtestnet]" in text
    assert ep.conf_has_proxy("liquid-testnet") is False


def test_write_conf_proxy_idempotent(tmp_path, monkeypatch):
    conf_dir = tmp_path / "config"
    conf_dir.mkdir()
    conf = conf_dir / "elements-mainnet.conf"
    conf.write_text(
        "chain=liquidv1\n\n[liquidv1]\nrpcport=7041\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(ep, "LIQUID_CONFIG_DIR", conf_dir)

    ep.write_conf_proxy("liquid-mainnet", enable=True, proxy_endpoint="10.0.0.1:19050")
    first = conf.read_text(encoding="utf-8")
    ep.write_conf_proxy("liquid-mainnet", enable=True, proxy_endpoint="10.0.0.1:19050")
    second = conf.read_text(encoding="utf-8")
    assert first.count("proxy=") == 1
    assert second.count("proxy=") == 1


def test_match_prefers_longer_elements_pattern():
    from proxy_manager.process_monitor import _match_app

    apps = ep.elements_default_apps()
    matched = _match_app(
        "elementsd",
        "elementsd -conf=/config/elements-mainnet-light.conf -datadir=/data",
        apps,
    )
    assert matched is not None
    assert matched.id == "liquid-mainnet-light"

    matched = _match_app(
        "elementsd",
        "elementsd -conf=/config/elements-mainnet.conf -datadir=/data",
        apps,
    )
    assert matched is not None
    assert matched.id == "liquid-mainnet"

    matched = _match_app(
        "elementsd",
        "elementsd -conf=/config/elements.conf -datadir=/data",
        apps,
    )
    assert matched is not None
    assert matched.id == "liquid-testnet"


def test_config_migration_adds_liquid_apps(tmp_path):
    import json

    from proxy_manager.config import ConfigStore

    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "config_version": 15,
                "proxy": {},
                "apps": [
                    {
                        "id": "firefox",
                        "name": "Firefox",
                        "patterns": ["firefox"],
                        "use_proxy": False,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    store = ConfigStore(path=path)
    assert store.get_app("liquid-mainnet") is not None
    assert store.get_app("liquid-testnet") is not None
    assert store.get_app("liquid-mainnet-light") is not None
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["config_version"] >= 16


def test_presets_include_liquid():
    from proxy_manager.presets import default_apps

    ids = {a.id for a in default_apps()}
    assert "liquid-mainnet" in ids
    assert "liquid-testnet" in ids
    assert "liquid-mainnet-light" in ids


def test_pid_is_bridge_process_rejects_unrelated_pid():
    # O processo de teste atual não é o bridge pproxy — não deve validar.
    assert ep._pid_is_bridge_process(os.getpid()) is False


def test_read_bridge_pid_cleans_up_stale_file(tmp_path, monkeypatch):
    """PID reaproveitado por outro processo após reboot não deve enganar o bridge_running()."""
    pid_file = tmp_path / "bridge.pid"
    pid_file.write_text(str(os.getpid()), encoding="utf-8")
    monkeypatch.setattr(ep, "BRIDGE_PID_FILE", pid_file)

    assert ep._read_bridge_pid() is None
    assert not pid_file.exists()


def test_elements_process_status_reflects_bridge_health(monkeypatch):
    """Status da lista de processos reflete saúde real do bridge/Tor, não só o conf."""
    app = AppRule(id="liquid-mainnet", name="Liquid Mainnet", patterns=[])

    monkeypatch.setattr(ep, "conf_has_proxy", lambda app_id: False)
    assert ep.elements_process_status(app) == (False, "sem proxy")

    monkeypatch.setattr(ep, "conf_has_proxy", lambda app_id: True)
    monkeypatch.setattr(ep, "elements_bridge_healthy", lambda: False)
    assert ep.elements_process_status(app) == (False, "Tor no conf, bridge caiu ⚠")

    monkeypatch.setattr(ep, "elements_bridge_healthy", lambda: True)
    assert ep.elements_process_status(app) == (True, "proxy ativo ✓")

    non_elements = AppRule(id="firefox", name="Firefox", patterns=["firefox"])
    assert ep.elements_process_status(non_elements) == (None, None)


def test_any_target_proxy_configured(monkeypatch):
    monkeypatch.setattr(ep, "conf_has_proxy", lambda app_id: app_id == "liquid-testnet")
    assert ep.any_target_proxy_configured() is True

    monkeypatch.setattr(ep, "conf_has_proxy", lambda app_id: False)
    assert ep.any_target_proxy_configured() is False


def test_bridge_watchdog_idles_without_configured_target(monkeypatch):
    """Sem nenhum app Elements ativo, o watchdog não deve tentar reiniciar nada."""
    monkeypatch.setattr(ep, "_WATCHDOG_INTERVAL", 0.05)
    monkeypatch.setattr(ep, "any_target_proxy_configured", lambda: False)

    events: list[tuple[bool, str]] = []
    try:
        ep.start_bridge_watchdog(lambda: [], events.append)
        time.sleep(0.15)
    finally:
        ep.stop_bridge_watchdog()
    assert events == []


def test_bridge_watchdog_self_heals_when_bridge_down(monkeypatch):
    """Se o bridge cair enquanto um app Elements está configurado, o watchdog deve
    chamar ensure_tor_bridge() de novo e reportar o resultado via callback."""
    monkeypatch.setattr(ep, "_WATCHDOG_INTERVAL", 0.05)
    monkeypatch.setattr(ep, "any_target_proxy_configured", lambda: True)
    monkeypatch.setattr(ep, "elements_bridge_healthy", lambda: False)

    calls = {"n": 0}

    def fake_ensure():
        calls["n"] += 1
        return True, "bridge religado"

    monkeypatch.setattr(ep, "ensure_tor_bridge", fake_ensure)

    events: list[tuple[bool, str]] = []
    try:
        ep.start_bridge_watchdog(lambda: [], lambda ok, msg: events.append((ok, msg)))
        time.sleep(0.15)
    finally:
        ep.stop_bridge_watchdog()

    assert calls["n"] >= 1
    assert events and events[0] == (True, "bridge religado")
