from __future__ import annotations

import os
import time
from dataclasses import dataclass

import psutil

from proxy_manager.browser_proxy import (
    clear_firefox_profile_lock,
    is_browser_app,
    main_browser_command,
    prepare_browser_proxy,
)
from proxy_manager.claude_proxy import is_claude_app, prepare_claude_proxy
from proxy_manager.launcher import launch_command
from proxy_manager.models import AppRule, ProxySettings
from proxy_manager.network import AUTO_INTERFACE


@dataclass
class RelaunchResult:
    old_pid: int
    new_pid: int
    cmd: list[str]


def read_process_cmdline(pid: int) -> list[str]:
    try:
        proc = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return []
    cmdline = proc.cmdline()
    if cmdline:
        return cmdline
    with open(f"/proc/{pid}/cmdline", "rb") as f:
        parts = f.read().split(b"\0")
    return [p.decode(errors="replace") for p in parts if p]


def terminate_process(pid: int, timeout: float = 5.0) -> None:
    try:
        proc = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return

    try:
        proc.terminate()
    except psutil.NoSuchProcess:
        return

    try:
        proc.wait(timeout=timeout)
        return
    except (psutil.TimeoutExpired, psutil.NoSuchProcess):
        pass

    try:
        proc.kill()
        proc.wait(timeout=1.0)
    except (psutil.NoSuchProcess, psutil.TimeoutExpired):
        pass


def _matches_app(name: str, cmdline: str, app: AppRule) -> bool:
    haystack = f"{name} {cmdline}".lower()
    return any(p.lower() in haystack for p in app.patterns)


def _alive_matching_pids(app: AppRule) -> list[int]:
    alive: list[int] = []
    for proc in psutil.process_iter(["pid", "name", "cmdline", "status"]):
        try:
            info = proc.info
            if info.get("status") == psutil.STATUS_ZOMBIE:
                continue
            name = info.get("name") or ""
            cmdline = " ".join(info.get("cmdline") or [])
            if _matches_app(name, cmdline, app):
                alive.append(info["pid"])
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return alive


def terminate_app_tree(app: AppRule, timeout: float = 12.0) -> None:
    if app.id == "firefox":
        import subprocess

        subprocess.run(["pkill", "-9", "-f", "firefox"], check=False)

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        pids = _alive_matching_pids(app)
        if not pids:
            return
        for pid in sorted(set(pids), reverse=True):
            terminate_process(pid, timeout=1.0)
        time.sleep(0.3)

    if _alive_matching_pids(app):
        raise RuntimeError(f"{app.name} não encerrou completamente. Feche manualmente e tente de novo.")


def relaunch_process(
    pid: int,
    *,
    app: AppRule | None = None,
    proxy: ProxySettings,
    use_proxy: bool,
    network_interface: str = AUTO_INTERFACE,
    terminate_first: bool = True,
) -> RelaunchResult:
    cmd = read_process_cmdline(pid)
    if not cmd:
        if app and app.command.strip():
            cmd = app.command.split()
        elif pid <= 0:
            if not app or not app.command.strip():
                raise RuntimeError("Nenhum processo e nenhum comando configurado.")
            cmd = app.command.split()
        else:
            raise RuntimeError(f"Processo {pid} não existe e nenhum comando padrão configurado.")

    if app and is_claude_app(app):
        prepare_claude_proxy(proxy, use_proxy)
    elif app and is_browser_app(app):
        cmd = main_browser_command(app, cmd)
        prepare_browser_proxy(app, proxy, use_proxy)

    base_env: dict[str, str] | None = None
    if not terminate_first:
        try:
            base_env = dict(psutil.Process(pid).environ())
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            base_env = None

    if terminate_first:
        if app and is_browser_app(app):
            terminate_app_tree(app)
            if app.id == "firefox":
                clear_firefox_profile_lock()
        elif pid > 0:
            terminate_process(pid)
            time.sleep(0.4)
            if os.path.exists(f"/proc/{pid}"):
                raise RuntimeError(f"O processo {pid} não encerrou. Tente fechar manualmente.")

    proc = launch_command(
        cmd,
        proxy=proxy,
        use_proxy=use_proxy,
        network_interface=network_interface,
        base_env=base_env,
        app=app,
    )
    return RelaunchResult(old_pid=pid, new_pid=proc.pid, cmd=cmd)
