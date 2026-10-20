from datetime import datetime, timedelta

from proxy_manager.models import ProxySettings
from proxy_manager.proxy_health import activation_failure_detail
from proxy_manager.tor_country import diagnose_tor_log

_OUTDATED = """
At least one protocol listed as recommended in the consensus is not supported by this version of Tor. You should upgrade. This version of Tor will eventually stop working as a client on the Tor network. The missing protocols are: Desc=3-4 Microdesc=3 Relay=5-6
Bootstrapped 95% (circuit_create): Establishing a Tor circuit
Problem bootstrapping. Stuck at 95% (circuit_create): Establishing a Tor circuit.
Bootstrapped 100% (done): Done
Guard FloTheDev ($5B11EA82ED4DFAD156AFB6ACF306CE0E710AEDF8) is failing an extremely large amount of circuits. Success counts are 72/299.
Tried for 120 seconds to get a connection to [scrubbed]:443. Giving up. (waiting for circuit)
"""


def test_outdated_client_explains_circuit_failure():
    msg = diagnose_tor_log(_OUTDATED, version="0.4.8.10")
    assert "0.4.8.10" in msg
    assert "Desc=3-4 Microdesc=3 Relay=5-6" in msg
    assert "circuitos expiram" in msg
    assert "preso no bootstrap" not in msg
    assert "systemctl restart tor@default" in msg


def test_stuck_bootstrap_without_later_success():
    log = "Problem bootstrapping. Stuck at 45% (requesting_descriptors): Asking for relay descriptors."
    msg = diagnose_tor_log(log, version="0.4.9.11")
    assert "45%" in msg
    assert "requesting descriptors" in msg
    assert "0.4.9.11" in msg


def test_recovered_bootstrap_is_not_a_failure():
    log = """
Problem bootstrapping. Stuck at 75% (enough_dirinfo): Loaded enough directory info to build circuits.
Bootstrapped 100% (done): Done
"""
    assert diagnose_tor_log(log) == ""


def test_protocol_warning_alone_is_not_a_live_failure():
    log = (
        "The missing protocols are: Desc=3-4 Microdesc=3 Relay=5-6\n"
        "Bootstrapped 100% (done): Done\n"
    )
    assert diagnose_tor_log(log, version="0.4.8.10") == ""
    failed = diagnose_tor_log(log, version="0.4.8.10", traffic_failed=True)
    assert "não suporta os protocolos" in failed


def test_bind_error():
    msg = diagnose_tor_log(
        "Tor version 0.4.9.11.\nCould not bind to 127.0.0.1:9050: Address already in use"
    )
    assert "Address already in use" in msg
    assert "0.4.9.11" in msg


def test_guard_failure_names_the_guard():
    log = (
        "Opening Socks listener on 127.0.0.1:9050\n"
        "Tor 0.4.9.11 running on Linux\n"
        "Guard Parzival ($AAA) is failing an extremely large amount of circuits. "
        "Success counts are 4/80.\n"
    )
    msg = diagnose_tor_log(log)
    assert "0.4.9.11" in msg
    assert "Parzival" in msg
    assert "4/80" in msg


def test_ignores_previous_process_and_reports_current_timeout():
    log = """
The missing protocols are: Desc=3-4 Microdesc=3 Relay=5-6
Tried for 120 seconds to get a connection to [scrubbed]:443. Giving up.
Opening Socks listener on 127.0.0.1:9050
Tor 0.4.9.11 running on Linux with Libevent 2.1.12-stable.
Bootstrapped 100% (done): Done
No circuits are opened. Relaxed timeout for circuit 7. However, it appears the circuit has timed out anyway.
"""
    assert diagnose_tor_log(log) == ""
    msg = diagnose_tor_log(log, traffic_failed=True)
    assert "0.4.9.11" in msg
    assert "circuitos não fecham" in msg
    assert "protocolos" not in msg
    assert "120" not in msg


def test_activation_failure_uses_tor_cause(monkeypatch):
    monkeypatch.setattr(
        "proxy_manager.tor_country.tor_failure_hint",
        lambda port, traffic_failed=False, force=False: "Tor 0.4.8.10 sem protocolos.",
    )
    proxy = ProxySettings(source="tor", upstream_port=9050)
    msg = activation_failure_detail(proxy, "Proxy local ligado, mas o tráfego não passou.")
    assert msg == "Tor 0.4.8.10 sem protocolos."


def test_connectreset_names_the_network_reset():
    log = """
Opening Socks listener on 127.0.0.1:9050
Problem bootstrapping. Stuck at 14% (handshake): Handshaking with a relay. (CONNECTRESET; CONNECTRESET; count 1173; recommendation warn; host ABC at 1.2.3.4:9001)
1173 connections have failed:
 1140 connections died in state handshaking (Tor, v3 handshake) with SSL state SSL negotiation finished successfully in OPEN
"""
    msg = diagnose_tor_log(log, version="0.4.9.11")
    assert "14%" in msg
    assert "handshake" in msg
    assert "CONNECTRESET" in msg
    assert "1173" in msg
    assert "relays" in msg


def test_connectreset_before_success_is_not_a_failure():
    log = """
Problem bootstrapping. Stuck at 14% (handshake): Handshaking with a relay. (CONNECTRESET; CONNECTRESET; count 20; recommendation warn)
Bootstrapped 100% (done): Done
"""
    assert diagnose_tor_log(log, version="0.4.9.11") == ""


def test_guard_before_bootstrap_done_is_not_a_live_failure():
    log = """
Guard Parzival ($AAA) is failing an extremely large amount of circuits. Success counts are 4/80.
Bootstrapped 100% (done): Done
"""
    assert diagnose_tor_log(log) == ""


def test_settings_configured_tor_mode_ignores_circuit_health(monkeypatch):
    """Circuito ruim não desconfigura o modo — senão o toggle trava em auto-config."""
    from proxy_manager.proxy_sources import settings_configured

    monkeypatch.setattr(
        "proxy_manager.proxy_sources.tor_circuit_block",
        lambda port=9050: "rede derruba o handshake (CONNECTRESET)",
    )
    ok, msg = settings_configured(ProxySettings(source="tor", upstream_port=9050))
    assert ok is True
    assert msg == "ok"


def test_settings_configured_accepts_tor_source():
    from proxy_manager.proxy_sources import settings_configured

    ok, msg = settings_configured(ProxySettings(source="tor", upstream_port=9050))
    assert ok is True
    assert msg == "ok"


def test_auto_configure_tor_does_not_apply_when_circuit_is_blocked(monkeypatch):
    from proxy_manager.proxy_sources import _auto_configure_tor

    monkeypatch.setattr("proxy_manager.tor_country.TOR_EXIT_COUNTRY_ENABLED", False)
    monkeypatch.setattr("proxy_manager.tor_country.stop_managed_tor", lambda: None)
    monkeypatch.setattr(
        "proxy_manager.proxy_sources.try_start_tor",
        lambda port=9050: (True, "porta aberta"),
    )
    monkeypatch.setattr(
        "proxy_manager.proxy_sources.tor_circuit_block",
        lambda port=9050: "rede derruba o handshake (CONNECTRESET)",
    )
    proxy = ProxySettings(source="direct", auto_proxy_mode="tor")
    ok, msg = _auto_configure_tor(proxy)
    assert ok is False
    assert "CONNECTRESET" in msg
    assert proxy.source == "direct"


def test_auto_configure_tor_applies_when_log_is_clean(monkeypatch):
    from proxy_manager.proxy_sources import _auto_configure_tor

    monkeypatch.setattr("proxy_manager.tor_country.TOR_EXIT_COUNTRY_ENABLED", False)
    monkeypatch.setattr("proxy_manager.tor_country.stop_managed_tor", lambda: None)
    monkeypatch.setattr(
        "proxy_manager.proxy_sources.try_start_tor",
        lambda port=9050: (True, "porta aberta"),
    )
    monkeypatch.setattr(
        "proxy_manager.proxy_sources.tor_circuit_block",
        lambda port=9050: "",
    )
    proxy = ProxySettings(source="direct", auto_proxy_mode="tor")
    ok, msg = _auto_configure_tor(proxy)
    assert ok is True
    assert proxy.source == "tor"
    assert proxy.upstream_port == 9050
    assert "9050" in msg


def test_wait_skips_ip_when_tor_log_already_explains(monkeypatch):
    from proxy_manager.proxy_health import PublicIpInfo, wait_for_proxy_verification

    def boom(*_a, **_k):
        raise AssertionError("não deveria consultar IP")

    monkeypatch.setattr("proxy_manager.proxy_health.fetch_public_ip_info_direct", boom)
    monkeypatch.setattr("proxy_manager.proxy_health.fetch_public_ip_info_via_proxy", boom)
    monkeypatch.setattr(
        "proxy_manager.proxy_sources.tor_circuit_block",
        lambda port=9050: "CONNECTRESET no handshake",
    )
    proxy = ProxySettings(source="tor", upstream_port=9050)
    ok, info, _direct, msg = wait_for_proxy_verification(
        proxy,
        direct_info=PublicIpInfo(ip="1.1.1.1"),
        max_attempts=3,
    )
    assert ok is False
    assert info.ip is None
    assert "CONNECTRESET" in msg


def test_activation_failure_keeps_non_tor_message():
    proxy = ProxySettings(source="free", upstream_host="1.2.3.4", upstream_port=8080)
    fallback = "Proxy local ligado, mas o tráfego não passou."
    assert activation_failure_detail(proxy, fallback) == fallback


def _iso(when: datetime) -> str:
    return when.astimezone().isoformat(timespec="seconds")


def test_old_timeout_after_bootstrap_does_not_block():
    now = datetime.now().astimezone()
    log = (
        f"{_iso(now - timedelta(hours=6))} host Tor[1]: Bootstrapped 100% (done): Done\n"
        f"{_iso(now - timedelta(minutes=50))} host Tor[1]: "
        "Tried for 1799 seconds to get a connection to [scrubbed]:80. Giving up.\n"
        f"{_iso(now)} host Tor[1]: Failed to find node for hop #1 of our path. Discarding this circuit.\n"
    )
    assert diagnose_tor_log(log, version="0.4.9.11", now=now) == ""


def test_recent_timeout_after_bootstrap_still_blocks():
    now = datetime.now().astimezone()
    log = (
        f"{_iso(now - timedelta(hours=1))} host Tor[1]: Bootstrapped 100% (done): Done\n"
        f"{_iso(now - timedelta(seconds=30))} host Tor[1]: "
        "Tried for 1799 seconds to get a connection to [scrubbed]:80. Giving up.\n"
    )
    msg = diagnose_tor_log(log, version="0.4.9.11", now=now)
    assert "1799" in msg
    assert "circuitos não fecham" in msg


def test_path_restriction_explains_only_failed_traffic():
    now = datetime.now().astimezone()
    log = (
        f"{_iso(now - timedelta(hours=1))} host Tor[1]: Bootstrapped 100% (done): Done\n"
        f"{_iso(now)} host Tor[1]: All current guards excluded by path restriction type 2; "
        "using an additional guard.\n"
        f"{_iso(now)} host Tor[1]: Failed to find node for hop #1 of our path. Discarding this circuit.\n"
    )
    assert diagnose_tor_log(log, now=now) == ""
    msg = diagnose_tor_log(log, version="0.4.9.11", traffic_failed=True, now=now)
    assert "hop 1" in msg
    assert "path restriction type 2" in msg


def test_repeated_path_restriction_blocks_even_without_failed_traffic():
    now = datetime.now().astimezone()
    lines = [f"{_iso(now - timedelta(hours=2))} host Tor[1]: Bootstrapped 100% (done): Done"]
    for i in range(5):
        when = _iso(now - timedelta(seconds=30 * (5 - i)))
        lines.append(
            f"{when} host Tor[1]: All current guards excluded by path restriction type 2; "
            "using an additional guard."
        )
        lines.append(
            f"{when} host Tor[1]: Failed to find node for hop #1 of our path. Discarding this circuit."
        )
    log = "\n".join(lines)
    msg = diagnose_tor_log(log, version="0.4.9.11", now=now)
    assert "hop 1" in msg
    assert "path restriction type 2" in msg


def test_single_path_restriction_still_needs_failed_traffic():
    now = datetime.now().astimezone()
    log = (
        f"{_iso(now - timedelta(hours=1))} host Tor[1]: Bootstrapped 100% (done): Done\n"
        f"{_iso(now)} host Tor[1]: All current guards excluded by path restriction type 2; "
        "using an additional guard.\n"
        f"{_iso(now)} host Tor[1]: Failed to find node for hop #1 of our path. Discarding this circuit.\n"
    )
    assert diagnose_tor_log(log, now=now) == ""


def test_old_path_restriction_is_ignored_even_if_traffic_failed():
    now = datetime.now().astimezone()
    log = (
        f"{_iso(now - timedelta(hours=2))} host Tor[1]: Bootstrapped 100% (done): Done\n"
        f"{_iso(now - timedelta(minutes=40))} host Tor[1]: "
        "All current guards excluded by path restriction type 0; using an additional guard.\n"
        f"{_iso(now - timedelta(minutes=40))} host Tor[1]: "
        "Failed to find node for hop #1 of our path. Discarding this circuit.\n"
    )
    assert diagnose_tor_log(log, traffic_failed=True, now=now) == ""


def test_circuit_block_clears_when_socks_connects(monkeypatch):
    from proxy_manager.proxy_sources import tor_circuit_block

    monkeypatch.setattr("proxy_manager.proxy_sources.is_port_open", lambda host, port: True)
    monkeypatch.setattr(
        "proxy_manager.tor_country.tor_failure_hint",
        lambda port, traffic_failed=False, force=False: (
            "Tor 0.4.9.11 escuta, mas os circuitos não fecham (1799s)."
        ),
    )
    monkeypatch.setattr("proxy_manager.tor_country.socks_circuit_open", lambda port, timeout=2.5: True)
    noted: list[int] = []
    monkeypatch.setattr("proxy_manager.tor_country.note_live_circuit", lambda port: noted.append(port))
    assert tor_circuit_block(9050) == ""
    assert noted == [9050]


def test_circuit_block_keeps_stuck_bootstrap(monkeypatch):
    from proxy_manager.proxy_sources import tor_circuit_block

    monkeypatch.setattr("proxy_manager.proxy_sources.is_port_open", lambda host, port: True)
    monkeypatch.setattr(
        "proxy_manager.tor_country.tor_failure_hint",
        lambda port, traffic_failed=False, force=False: (
            "Tor 0.4.9.11 está preso no bootstrap 14% (handshake)."
        ),
    )

    def boom(port, timeout=2.5):
        raise AssertionError("bootstrap preso não deve abrir circuito de prova")

    monkeypatch.setattr("proxy_manager.tor_country.socks_circuit_open", boom)
    msg = tor_circuit_block(9050)
    assert "14%" in msg


def test_clock_jump_explains_the_timeout_it_caused():
    now = datetime.now().astimezone()
    log = (
        f"{_iso(now - timedelta(hours=2))} host Tor[1]: Bootstrapped 100% (done): Done\n"
        f"{_iso(now - timedelta(seconds=20))} host Tor[1]: "
        "Your system clock just jumped 641 seconds forward; "
        "assuming established circuits no longer work.\n"
        f"{_iso(now - timedelta(seconds=20))} host Tor[1]: "
        "Tried for 641 seconds to get a connection to [scrubbed]:80. Giving up. "
        "(waiting for circuit)\n"
    )
    msg = diagnose_tor_log(log, version="0.4.9.11", now=now)
    assert "relógio avançou 641s" in msg
    assert "circuitos não fecham" not in msg


def test_old_clock_jump_is_not_a_live_log_failure():
    now = datetime.now().astimezone()
    log = (
        f"{_iso(now - timedelta(hours=2))} host Tor[1]: Bootstrapped 100% (done): Done\n"
        f"{_iso(now - timedelta(minutes=40))} host Tor[1]: "
        "Your system clock just jumped 641 seconds forward; "
        "assuming established circuits no longer work.\n"
        f"{_iso(now - timedelta(minutes=40))} host Tor[1]: "
        "Tried for 641 seconds to get a connection to [scrubbed]:80. Giving up.\n"
    )
    assert diagnose_tor_log(log, now=now) == ""


def test_clock_jump_parser_keeps_recent_and_drops_old():
    from proxy_manager.tor_country import clock_jump_seconds_from_log

    now = datetime.now().astimezone()
    recent = (
        f"{_iso(now - timedelta(minutes=30))} host Tor[1]: "
        "Your system clock just jumped 641 seconds forward; "
        "assuming established circuits no longer work.\n"
    )
    old = (
        f"{_iso(now - timedelta(hours=5))} host Tor[1]: "
        "Your system clock just jumped 99 seconds forward; "
        "assuming established circuits no longer work.\n"
    )
    assert clock_jump_seconds_from_log(recent, now=now) == 641
    assert clock_jump_seconds_from_log(old, now=now) == 0
    assert clock_jump_seconds_from_log(old + recent, now=now) == 641


def test_circuit_block_uses_clock_jump_after_log_window(monkeypatch):
    from proxy_manager.proxy_sources import tor_circuit_block

    monkeypatch.setattr("proxy_manager.proxy_sources.is_port_open", lambda host, port: True)
    monkeypatch.setattr(
        "proxy_manager.tor_country.tor_failure_hint",
        lambda port, traffic_failed=False, force=False: "",
    )
    monkeypatch.setattr(
        "proxy_manager.tor_country.clock_jump_block",
        lambda port: "Tor descartou os circuitos: o relógio avançou 641s",
    )
    msg = tor_circuit_block(9050)
    assert "relógio avançou" in msg


def test_auto_configure_replaces_tor_after_clock_jump(monkeypatch):
    from proxy_manager.proxy_sources import _auto_configure_tor

    monkeypatch.setattr("proxy_manager.tor_country.TOR_EXIT_COUNTRY_ENABLED", True)
    monkeypatch.setattr(
        "proxy_manager.proxy_sources.ensure_tor_for_country",
        lambda country="": (True, "sistema", 9050),
    )
    calls = {"n": 0}

    def _block(port=9050):
        calls["n"] += 1
        # Primeira checagem (sistema/antes do adopt) ainda vê o salto do relógio;
        # depois do adopt a 9050 já tem circuito.
        if calls["n"] == 1:
            return (
                "Tor descartou os circuitos: o relógio avançou 641s "
                "(suspensão ou ajuste do relógio)."
            )
        return ""

    monkeypatch.setattr("proxy_manager.proxy_sources.tor_circuit_block", _block)
    monkeypatch.setattr(
        "proxy_manager.tor_country.adopt_fresh_tor_after_clock_jump",
        lambda: (True, "Tor novo em 127.0.0.1:9050", 9050),
    )
    proxy = ProxySettings(source="direct", auto_proxy_mode="tor")
    ok, msg = _auto_configure_tor(proxy)
    assert ok is True
    assert proxy.source == "tor"
    assert proxy.upstream_port == 9050
    assert "9050" in msg


def test_adopt_restarts_managed_tor_when_circuit_stays_down(monkeypatch):
    from proxy_manager.tor_country import adopt_fresh_tor_after_clock_jump

    started: list[str] = []
    monkeypatch.setattr("proxy_manager.tor_country.is_port_open", lambda host, port: True)
    monkeypatch.setattr("proxy_manager.tor_country._read_pid", lambda: 123)
    monkeypatch.setattr("proxy_manager.tor_country.clear_tor_hint_cache", lambda: None)
    monkeypatch.setattr(
        "proxy_manager.tor_country.start_managed_tor",
        lambda country="": started.append(country) or (True, "up"),
    )
    monkeypatch.setattr("proxy_manager.tor_country.wait_for_socks_circuit", lambda port, timeout=45: False)
    monkeypatch.setattr(
        "proxy_manager.tor_country.tor_failure_hint",
        lambda port, traffic_failed=False, force=False: "preso no bootstrap 14% (handshake): CONNECTRESET",
    )
    ok, msg, port = adopt_fresh_tor_after_clock_jump()
    assert ok is False
    assert started == [""]
    assert port == 9050
    assert "CONNECTRESET" in msg
    assert "relógio avançou" in msg


def test_adopt_starts_managed_tor_and_switches_port(monkeypatch):
    from proxy_manager.tor_country import adopt_fresh_tor_after_clock_jump

    monkeypatch.setattr("proxy_manager.tor_country.is_port_open", lambda host, port: False)
    monkeypatch.setattr("proxy_manager.tor_country._read_pid", lambda: None)
    monkeypatch.setattr("proxy_manager.tor_country.stop_managed_tor", lambda: None)
    monkeypatch.setattr("proxy_manager.tor_country.clear_tor_hint_cache", lambda: None)
    monkeypatch.setattr(
        "proxy_manager.tor_country.start_managed_tor",
        lambda country="": (True, "up"),
    )
    monkeypatch.setattr("proxy_manager.tor_country.wait_for_socks_circuit", lambda port, timeout=45: True)
    noted: list[int] = []
    monkeypatch.setattr("proxy_manager.tor_country.note_live_circuit", lambda port: noted.append(port))
    ok, msg, port = adopt_fresh_tor_after_clock_jump()
    assert ok is True
    assert port == 9050
    assert noted == [9050]
    assert "9050" in msg


def test_live_circuit_hides_soft_hint_until_traffic_fails(monkeypatch):
    from proxy_manager.tor_country import clear_tor_hint_cache, note_live_circuit, tor_failure_hint

    clear_tor_hint_cache()
    monkeypatch.setattr(
        "proxy_manager.tor_country._recent_tor_log",
        lambda port: "Tried for 90 seconds to get a connection to [scrubbed]:443. Giving up.\n",
    )
    monkeypatch.setattr("proxy_manager.tor_country._tor_version", lambda: "0.4.9.11")
    blocked = tor_failure_hint(9050, force=True)
    assert "90" in blocked
    note_live_circuit(9050)
    assert tor_failure_hint(9050, force=True) == ""
    failed = tor_failure_hint(9050, traffic_failed=True, force=True)
    assert "90" in failed
    clear_tor_hint_cache()
