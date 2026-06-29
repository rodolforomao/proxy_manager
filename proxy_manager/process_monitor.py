from __future__ import annotations

import psutil

from proxy_manager.browser_proxy import browser_proxy_active, is_content_process
from proxy_manager.claude_proxy import claude_proxy_active
from proxy_manager.models import AppRule, ProcessInfo, ProxySettings
from proxy_manager.network import detect_process_interface
from proxy_manager.proxy_env import classify_process_status, read_process_proxy_env


def _match_app(name: str, cmdline: str, apps: list[AppRule]) -> AppRule | None:
    best: AppRule | None = None
    best_score = -1
    haystack = f"{name} {cmdline}".lower()
    for app in apps:
        if not app.enabled:
            continue
        for pattern in app.patterns:
            p = pattern.lower()
            if p in haystack:
                score = len(p)
                if score > best_score:
                    best_score = score
                    best = app
                break
    return best


def scan_processes(
    apps: list[AppRule],
    proxy: ProxySettings,
    *,
    detect_network: bool = False,
) -> list[ProcessInfo]:
    results: list[ProcessInfo] = []
    best_per_app: dict[str, ProcessInfo] = {}

    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            info = proc.info
            pid = info["pid"]

            name = info.get("name") or ""
            cmdline_list = info.get("cmdline") or []
            cmdline = " ".join(cmdline_list)

            if not name and not cmdline:
                continue

            matched = _match_app(name, cmdline, apps)
            if matched is None:
                continue

            proxy_env = read_process_proxy_env(pid)
            special_active = claude_proxy_active(matched, cmdline, proxy_env=proxy_env)
            if special_active is None:
                special_active = browser_proxy_active(matched, cmdline)
            if special_active is not None:
                active = special_active
                status = "proxy ativo ✓" if active else "sem proxy"
            else:
                active, status = classify_process_status(matched, proxy_env, proxy)
            iface = None
            if matched.network_interface != "auto":
                iface = matched.network_interface
            elif detect_network:
                iface = detect_process_interface(pid)

            entry = ProcessInfo(
                pid=pid,
                name=name,
                cmdline=cmdline[:120],
                matched_app=matched,
                proxy_env=proxy_env,
                proxy_active=active,
                status=status,
                network_interface=iface,
            )

            prev = best_per_app.get(matched.id)
            if prev is None or _is_better_process(entry, prev):
                best_per_app[matched.id] = entry
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue

    results = list(best_per_app.values())
    results.sort(key=lambda p: (p.matched_app.name if p.matched_app else "", p.pid))
    return results


def find_process_for_app(
    app: AppRule,
    apps: list[AppRule],
    proxy: ProxySettings,
    processes: list[ProcessInfo] | None = None,
) -> ProcessInfo | None:
    items = processes if processes is not None else scan_processes(apps, proxy)
    for proc in items:
        if proc.matched_app and proc.matched_app.id == app.id:
            return proc
    return None


def _process_score(proc: ProcessInfo) -> int:
    score = len(proc.cmdline)
    name = proc.name.lower()
    cmd = proc.cmdline.lower()

    if is_content_process(cmd):
        score -= 10_000
    if name in ("firefox", "chrome", "google-chrome", "chromium", "chromium-browser"):
        score += 5_000
    if proc.proxy_active:
        score += 100
    return score


def _is_better_process(candidate: ProcessInfo, current: ProcessInfo) -> bool:
    return _process_score(candidate) > _process_score(current)


def summary_counts(processes: list[ProcessInfo], apps: list[AppRule]) -> dict[str, int]:
    with_proxy = sum(1 for p in processes if p.proxy_active)
    without_proxy = len(processes) - with_proxy
    return {
        "running": len(processes),
        "with_proxy": with_proxy,
        "without_proxy": without_proxy,
        "mismatches": 0,
    }
