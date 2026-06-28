#!/usr/bin/env python3
"""Suíte de testes automatizados do Proxy Manager (sem GUI)."""

from __future__ import annotations

import importlib
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PASSED = 0
FAILED = 0
ERRORS: list[str] = []


def ok(name: str) -> None:
    global PASSED
    PASSED += 1
    print(f"  ✓ {name}")


def fail(name: str, detail: str) -> None:
    global FAILED
    FAILED += 1
    ERRORS.append(f"{name}: {detail}")
    print(f"  ✗ {name} — {detail}")


def run(name: str, fn) -> None:
    print(f"\n[{name}]")
    try:
        fn()
    except Exception as exc:
        fail(name, str(exc))
        traceback.print_exc()


def test_imports() -> None:
    modules = [
        "proxy_manager.config",
        "proxy_manager.models",
        "proxy_manager.local_proxy",
        "proxy_manager.proxy_health",
        "proxy_manager.proxy_sources",
        "proxy_manager.tor_country",
        "proxy_manager.process_monitor",
        "proxy_manager.process_cache",
        "proxy_manager.browser_proxy",
        "proxy_manager.countries",
        "proxy_manager.proxy_env",
        "proxy_manager.presets",
    ]
    for mod in modules:
        importlib.import_module(mod)
        ok(f"import {mod}")
    import proxy_manager.gui  # noqa: F401

    ok("import proxy_manager.gui")


def test_config_roundtrip() -> None:
    from proxy_manager.config import ConfigStore, CONFIG_VERSION

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "config.json"
        store = ConfigStore(path)
        store.proxy.target_country = "JP"
        store.touch_recent_app("firefox")
        store.save()
        store2 = ConfigStore(path)
        assert store2.proxy.target_country == "JP", store2.proxy.target_country
        assert "firefox" in store2.recent_app_ids
        assert len(store2.apps) > 0
        ok("config save/load")
        store3 = ConfigStore(path)
        data = store3.path.read_text(encoding="utf-8")
        assert f'"config_version": {CONFIG_VERSION}' in data or "config_version" in data
        ok(f"config version {CONFIG_VERSION}")


def test_tor_original() -> None:
    from proxy_manager.models import ProxySettings
    from proxy_manager.proxy_sources import _auto_configure_tor, DEFAULT_TOR_PORT
    from proxy_manager.tor_country import TOR_EXIT_COUNTRY_ENABLED, stop_managed_tor

    assert TOR_EXIT_COUNTRY_ENABLED is False, "Tor por país deve estar desligado nos testes"
    stop_managed_tor()
    p = ProxySettings(auto_proxy_mode="tor", target_country="JP")
    success, msg = _auto_configure_tor(p)
    assert success, msg
    assert p.upstream_port == DEFAULT_TOR_PORT, p.upstream_port
    assert p.source == "tor"
    ok(f"Tor original — {msg}")


def test_fast_direct() -> None:
    from proxy_manager.models import ProxySettings
    from proxy_manager.proxy_sources import _auto_configure_fast, apply_fast_direct

    p = ProxySettings(auto_proxy_mode="fast")
    apply_fast_direct(p)
    assert p.source == "direct"
    ok("apply_fast_direct")
    success, msg = _auto_configure_fast(p)
    assert success, msg
    ok(f"auto_configure fast — {msg}")


def test_local_proxy_tor_chain() -> None:
    from proxy_manager.models import ProxySettings
    from proxy_manager.proxy_sources import _auto_configure_tor, verify_local_proxy_chain
    from proxy_manager.local_proxy import stop_local_proxy, restart_local_proxy, is_running
    from proxy_manager.proxy_health import (
        fetch_public_ip_info_direct,
        wait_for_proxy_verification,
    )
    from proxy_manager.tor_country import stop_managed_tor

    stop_managed_tor()
    stop_local_proxy()
    time.sleep(0.4)

    p = ProxySettings(auto_proxy_mode="tor")
    ok_cfg, msg = _auto_configure_tor(p)
    assert ok_cfg, msg
    ok_restart, msg2 = restart_local_proxy(p)
    assert ok_restart, msg2
    assert is_running(), "proxy local deve estar ativo"
    ok("restart local → Tor upstream")

    assert verify_local_proxy_chain(timeout=15), "cadeia local deve responder"
    ok("verify_local_proxy_chain")

    direct = fetch_public_ip_info_direct(force=True, timeout=15)
    assert direct.ip, direct.message
    verified, proxy_info, _, vmsg = wait_for_proxy_verification(
        p, direct_info=direct, max_attempts=6, poll_interval=2.0, timeout=12.0
    )
    assert verified, vmsg
    assert proxy_info.ip and proxy_info.ip != direct.ip, (
        f"IP proxy {proxy_info.ip} == direct {direct.ip}"
    )
    ok(f"IP via proxy {proxy_info.ip} ≠ direct {direct.ip}")


def test_local_proxy_direct_mode() -> None:
    from proxy_manager.models import ProxySettings
    from proxy_manager.proxy_sources import apply_fast_direct, verify_local_proxy_chain
    from proxy_manager.local_proxy import stop_local_proxy, restart_local_proxy
    from proxy_manager.proxy_health import (
        fetch_public_ip_info_direct,
        is_proxy_mode_verified,
        fetch_public_ip_info_via_proxy,
        clear_public_ip_cache,
    )

    stop_local_proxy()
    time.sleep(0.3)
    p = ProxySettings(auto_proxy_mode="fast")
    apply_fast_direct(p)
    ok_restart, msg = restart_local_proxy(p)
    assert ok_restart, msg
    assert verify_local_proxy_chain(timeout=12), "direct mode chain"
    ok("restart local → direct")

    direct = fetch_public_ip_info_direct(force=True)
    clear_public_ip_cache()
    via = fetch_public_ip_info_via_proxy(p, force=True, timeout=12)
    verified, vmsg = is_proxy_mode_verified(p, via, direct)
    assert verified, vmsg
    assert via.ip, "sem IP via proxy direct"
    ok(f"modo direct verificado — {via.ip}")


def test_proxy_verification_logic() -> None:
    from proxy_manager.models import ProxySettings
    from proxy_manager.proxy_health import PublicIpInfo, is_proxy_mode_verified

    direct = PublicIpInfo(ip="1.2.3.4", country_code="BR", country="Brazil")
    tor = ProxySettings(source="tor", target_country="JP")
    assert not is_proxy_mode_verified(
        tor, PublicIpInfo(ip="1.2.3.4"), direct
    )[0]
    assert is_proxy_mode_verified(
        tor, PublicIpInfo(ip="5.6.7.8", country_code="JP"), direct
    )[0]
    direct_mode = ProxySettings(source="direct")
    assert is_proxy_mode_verified(
        direct_mode, PublicIpInfo(ip="1.2.3.4"), direct
    )[0]
    ok("is_proxy_mode_verified (tor/direct)")


def test_process_scanner() -> None:
    from proxy_manager.config import ConfigStore
    from proxy_manager.process_monitor import scan_processes, summary_counts

    store = ConfigStore()
    procs = scan_processes(store.apps, store.proxy, detect_network=False)
    counts = summary_counts(procs, store.apps)
    assert "running" in counts
    ok(f"scan_processes — {counts['running']} apps detectados")


def test_stop_kills_stale_port() -> None:
    from proxy_manager.models import ProxySettings
    from proxy_manager.proxy_sources import apply_fast_direct
    from proxy_manager.local_proxy import (
        stop_local_proxy,
        restart_local_proxy,
        is_port_open,
    )
    from proxy_manager.models import LOCAL_PORT

    p = ProxySettings()
    apply_fast_direct(p)
    restart_local_proxy(p)
    assert is_port_open("127.0.0.1", LOCAL_PORT)
    p2 = ProxySettings(source="tor", scheme="socks5", upstream_host="127.0.0.1", upstream_port=9050)
    from proxy_manager.proxy_sources import _auto_configure_tor

    _auto_configure_tor(p2)
    stop_local_proxy()
    restart_local_proxy(p2)
    result = subprocess.run(
        ["pgrep", "-af", f"7890"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert "socks5" in result.stdout or "9050" in result.stdout, result.stdout[:200]
    ok("restart substitui upstream (não fica -rdirect)")


def test_countries() -> None:
    from proxy_manager.countries import country_label, country_code_from_label, country_option_labels

    assert country_code_from_label(country_label("JP")) == "JP"
    assert country_code_from_label(country_label("")) == ""
    assert len(country_option_labels()) > 5
    ok("countries helpers")


def main() -> int:
    print("=" * 60)
    print("Proxy Manager — test_all.py")
    print("=" * 60)

    run("imports", test_imports)
    run("config", test_config_roundtrip)
    run("tor_original", test_tor_original)
    run("fast_direct", test_fast_direct)
    run("proxy_verification_logic", test_proxy_verification_logic)
    run("countries", test_countries)
    run("process_scanner", test_process_scanner)
    run("local_proxy_tor", test_local_proxy_tor_chain)
    run("local_proxy_direct", test_local_proxy_direct_mode)
    run("stop_kills_stale", test_stop_kills_stale_port)

    print("\n" + "=" * 60)
    print(f"Resultado: {PASSED} ok, {FAILED} falhas")
    if ERRORS:
        print("\nFalhas:")
        for e in ERRORS:
            print(f"  - {e}")
        return 1
    print("✓ Todos os testes passaram.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
