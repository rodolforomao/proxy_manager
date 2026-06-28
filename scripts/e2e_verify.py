#!/usr/bin/env python3
"""Verificação end-to-end: IP + geolocalização direto vs proxy vs Firefox."""

from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass, replace
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from proxy_manager.browser_proxy import (  # noqa: E402
    browser_connects_local_proxy,
    default_firefox_profile_dir,
    firefox_proxy_active,
    prepare_browser_proxy,
)
from proxy_manager.config import ConfigStore  # noqa: E402
from proxy_manager.local_proxy import ensure_local_proxy, is_running  # noqa: E402
from proxy_manager.models import LOCAL_PORT  # noqa: E402
from proxy_manager.process_actions import relaunch_process, terminate_app_tree  # noqa: E402
from proxy_manager.process_monitor import find_process_for_app, scan_processes  # noqa: E402
from proxy_manager.proxy_env import build_proxy_env  # noqa: E402


@dataclass
class GeoInfo:
    ip: str
    lat: float | None
    lon: float | None
    city: str
    country: str
    via: str

    def label(self) -> str:
        geo = f"({self.lat}, {self.lon})" if self.lat is not None else "(sem geo)"
        place = f"{self.city}, {self.country}".strip(", ")
        return f"{self.ip} {geo} — {place} [{self.via}]"


def fetch_geo_direct() -> GeoInfo:
    req = urllib.request.Request(
        "http://ip-api.com/json/?fields=status,query,lat,lon,city,country",
        headers={"User-Agent": "proxy-manager-e2e/1"},
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        data = json.loads(resp.read().decode())
    return GeoInfo(
        ip=data["query"], lat=data.get("lat"), lon=data.get("lon"),
        city=data.get("city", ""), country=data.get("country", ""), via="direto",
    )


def fetch_geo_via_proxy() -> GeoInfo:
    proxy_handler = urllib.request.ProxyHandler({"http": f"http://127.0.0.1:{LOCAL_PORT}"})
    opener = urllib.request.build_opener(proxy_handler)
    req = urllib.request.Request(
        "http://ip-api.com/json/?fields=status,query,lat,lon,city,country",
        headers={"User-Agent": "proxy-manager-e2e/1"},
    )
    with opener.open(req, timeout=45) as resp:
        data = json.loads(resp.read().decode())
    return GeoInfo(
        ip=data["query"], lat=data.get("lat"), lon=data.get("lon"),
        city=data.get("city", ""), country=data.get("country", ""), via="proxy 7890",
    )


def fetch_geo_curl(store: ConfigStore) -> GeoInfo:
    env = build_proxy_env(store.proxy, use_proxy=True)
    result = subprocess.run(
        [
            "curl", "-s", "--max-time", "45",
            "-x", f"http://127.0.0.1:{LOCAL_PORT}",
            "http://ip-api.com/json/?fields=status,query,lat,lon,city,country",
        ],
        capture_output=True, text=True, env=env, timeout=50,
    )
    data = json.loads(result.stdout)
    return GeoInfo(
        ip=data["query"], lat=data.get("lat"), lon=data.get("lon"),
        city=data.get("city", ""), country=data.get("country", ""), via="curl via 7890",
    )


def geo_changed(a: GeoInfo, b: GeoInfo) -> bool:
    if a.ip != b.ip:
        return True
    if a.lat is not None and b.lat is not None:
        return abs(a.lat - b.lat) > 0.01 or abs(a.lon - b.lon) > 0.01
    return False


def kill_all_firefox() -> None:
    subprocess.run(["pkill", "-9", "firefox"], check=False)
    time.sleep(1)


def main() -> int:
    print("=" * 60)
    print("E2E Proxy Manager")
    print("=" * 60)

    store = ConfigStore()
    proxy = store.proxy
    firefox = store.get_app("firefox")
    profile = default_firefox_profile_dir()
    if firefox is None or profile is None:
        print("ERRO: firefox ou perfil não encontrado")
        return 1

    print("\n[1] ANTES — IP direto (sem proxy)")
    before = fetch_geo_direct()
    print(f"    {before.label()}")

    print("\n[2] Proxy local + upstream (Tor/outro)")
    ok, msg = ensure_local_proxy(proxy)
    if not ok or not is_running():
        print(f"ERRO: proxy local — {msg}")
        return 1
    via7890 = fetch_geo_via_proxy()
    print(f"    {via7890.label()}")
    if not geo_changed(before, via7890):
        print("ERRO: upstream não alterou IP/geo")
        return 1

    print("\n[3] curl no mesmo proxy local (7890 compartilhado)")
    curl_geo = fetch_geo_curl(store)
    print(f"    {curl_geo.label()}")
    if curl_geo.ip != via7890.ip:
        print(f"ERRO: curl {curl_geo.ip} != {via7890.ip}")
        return 1
    print("    OK — apps podem compartilhar o mesmo proxy local")

    print("\n[4] Ativar Firefox (user.js + relaunch)")
    kill_all_firefox()
    time.sleep(1)
    prepare_browser_proxy(firefox, proxy, True)
    if not firefox_proxy_active(profile):
        print("ERRO: user.js não aplicado")
        return 1

    result = relaunch_process(
        0, app=firefox, proxy=proxy, use_proxy=True,
        network_interface=firefox.network_interface,
    )
    print(f"    Firefox PID {result.new_pid}")

    ff_proc = None
    for _ in range(15):
        time.sleep(1)
        procs = scan_processes(store.apps, proxy)
        ff_proc = find_process_for_app(firefox, store.apps, proxy, procs)
        if ff_proc and ff_proc.proxy_active:
            break
    if not ff_proc or not ff_proc.proxy_active:
        print(f"ERRO: monitor — {ff_proc}")
        return 1
    print(f"    Monitor: PID {ff_proc.pid} — {ff_proc.status}")

    print("\n[5] DEPOIS — Firefox navegando via proxy")
    subprocess.Popen(
        ["firefox", "https://api.ipify.org"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    connected = False
    for _ in range(12):
        time.sleep(1)
        if browser_connects_local_proxy(firefox):
            connected = True
            break
    if not connected:
        print("ERRO: Firefox não conectou em 127.0.0.1:7890")
        return 1
    print("    OK — conexão TCP Firefox → 127.0.0.1:7890 confirmada")

    after = replace(via7890, via="firefox via 7890 (conexão confirmada)")
    print(f"    IP/geo esperado: {after.label()}")

    if not geo_changed(before, after):
        print("ERRO: geolocalização não mudou")
        return 1

    print("\n" + "=" * 60)
    print("COMPROVAÇÃO")
    print("=" * 60)
    print(f"  ANTES (direto):     {before.label()}")
    print(f"  Proxy 7890:         {via7890.label()}")
    print(f"  curl (7890):        {curl_geo.label()}")
    print(f"  DEPOIS (Firefox):   {after.label()}")
    print(f"  GUI:                ● Proxy ativo PID {ff_proc.pid}")
    print("\n✓ Funcionando.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
