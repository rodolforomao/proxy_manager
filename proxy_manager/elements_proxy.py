"""Tor nativo no Elements/Liquid (mainnet, testnet, light) via conf + Docker.

O elementsd não usa HTTP_PROXY. Este módulo:
1. Expõe um bridge SOCKS5 em 0.0.0.0:19050 → Tor local :9050 (acessível do Docker)
2. Grava `proxy=` no conf do Elements (bloco gerenciado)
3. Recria o container se já estiver rodando
"""
from __future__ import annotations

import os
import re
import signal
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import psutil

import proxy_manager.service_status as svc
from proxy_manager.local_proxy import is_port_open
from proxy_manager.models import AppRule
from proxy_manager.proxy_sources import DEFAULT_TOR_PORT, tor_status, try_start_tor

LIQUID_COMPOSE_DIR = Path("/home/black/enviroment/tmp/crypto/liquid_testnet")
LIQUID_CONFIG_DIR = LIQUID_COMPOSE_DIR / "config"

BRIDGE_PORT = 19050
BRIDGE_PID_FILE = Path.home() / ".config/proxy-manager/elements-tor-bridge.pid"
BRIDGE_LOG_FILE = Path.home() / ".config/proxy-manager/elements-tor-bridge.log"

_BEGIN = "# proxy-manager-tor-begin"
_END = "# proxy-manager-tor-end"
_BLOCK_RE = re.compile(
    rf"{re.escape(_BEGIN)}.*?{re.escape(_END)}\n?",
    re.DOTALL,
)


@dataclass(frozen=True)
class ElementsTarget:
    app_id: str
    name: str
    conf_file: str
    compose_service: str
    container_name: str
    compose_profile: str | None
    patterns: tuple[str, ...]
    notes: str


TARGETS: dict[str, ElementsTarget] = {
    "liquid-testnet": ElementsTarget(
        app_id="liquid-testnet",
        name="Liquid Testnet",
        conf_file="elements.conf",
        compose_service="elementsd",
        container_name="liquid-elementsd",
        compose_profile=None,
        patterns=("/config/elements.conf", "liquid-elementsd"),
        notes=(
            "Tor nativo no elementsd (proxy= SOCKS5). "
            "Não usa gost :7890. Edita elements.conf e recreia o container se estiver up."
        ),
    ),
    "liquid-mainnet": ElementsTarget(
        app_id="liquid-mainnet",
        name="Liquid Mainnet",
        conf_file="elements-mainnet.conf",
        compose_service="elementsd-mainnet",
        container_name="liquid-elementsd-mainnet",
        compose_profile="mainnet",
        patterns=("elements-mainnet.conf", "liquid-elementsd-mainnet"),
        notes=(
            "Tor nativo no elementsd mainnet full. "
            "Não usa gost :7890. Edita elements-mainnet.conf e recreia se estiver up."
        ),
    ),
    "liquid-mainnet-light": ElementsTarget(
        app_id="liquid-mainnet-light",
        name="Liquid Mainnet (leve)",
        conf_file="elements-mainnet-light.conf",
        compose_service="elementsd-mainnet-light",
        container_name="liquid-elementsd-mainnet-light",
        compose_profile="mainnet-light",
        patterns=("elements-mainnet-light.conf", "liquid-elementsd-mainnet-light"),
        notes=(
            "Tor nativo no elementsd mainnet pruned. "
            "Não usa gost :7890. Edita elements-mainnet-light.conf e recreia se estiver up."
        ),
    ),
}


def is_elements_app(app: AppRule | None) -> bool:
    return bool(app and app.id in TARGETS)


def elements_default_apps() -> list[AppRule]:
    return [
        AppRule(
            id=t.app_id,
            name=t.name,
            patterns=list(t.patterns),
            use_proxy=False,
            enabled=False,
            command="elements-tor",
            category="blockchain",
            notes=t.notes,
        )
        for t in TARGETS.values()
    ]


def get_target(app_id: str) -> ElementsTarget | None:
    return TARGETS.get(app_id)


def conf_path(app_id: str) -> Path | None:
    target = TARGETS.get(app_id)
    if not target:
        return None
    return LIQUID_CONFIG_DIR / target.conf_file


def conf_has_proxy(app_id: str) -> bool:
    path = conf_path(app_id)
    if path is None or not path.is_file():
        return False
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False
    if _BEGIN in text and _END in text:
        block = text[text.index(_BEGIN) : text.index(_END)]
        for line in block.splitlines():
            stripped = line.strip()
            if stripped.startswith("proxy=") and not stripped.startswith("#"):
                return True
        return False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("proxy=") and not stripped.startswith("#"):
            return True
    return False


def elements_bridge_healthy() -> bool:
    """Bridge SOCKS + Tor local realmente de pé (não só o que o conf diz)."""
    return bridge_running() and tor_status(DEFAULT_TOR_PORT)[0]


def elements_process_status(app: AppRule) -> tuple[bool, str] | tuple[None, None]:
    """(ativo, texto) para a lista de processos — reflete saúde real do bridge/Tor,
    não só a presença de `proxy=` no conf (que pode estar apontando pra um bridge morto)."""
    if not is_elements_app(app):
        return None, None
    if not conf_has_proxy(app.id):
        return False, "sem proxy"
    if elements_bridge_healthy():
        return True, "proxy ativo ✓"
    return False, "Tor no conf, bridge caiu ⚠"


def any_target_proxy_configured() -> bool:
    return any(conf_has_proxy(t.app_id) for t in TARGETS.values())


def docker_bridge_gateway(network: str = "liquid_testnet_default") -> str:
    try:
        result = subprocess.run(
            [
                "docker",
                "network",
                "inspect",
                network,
                "-f",
                "{{range .IPAM.Config}}{{.Gateway}}{{end}}",
            ],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
        gw = (result.stdout or "").strip()
        if gw and re.match(r"^\d+\.\d+\.\d+\.\d+$", gw):
            return gw
    except (OSError, subprocess.TimeoutExpired):
        pass
    return "172.19.0.1"


def _pid_is_bridge_process(pid: int) -> bool:
    """Confirma que o PID salvo ainda é o processo do bridge (evita falso-positivo por reuso de PID)."""
    try:
        cmdline = " ".join(psutil.Process(pid).cmdline()).lower()
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
        return False
    return "pproxy" in cmdline


def _read_bridge_pid() -> int | None:
    if not BRIDGE_PID_FILE.exists():
        return None
    try:
        pid = int(BRIDGE_PID_FILE.read_text(encoding="utf-8").strip())
    except ValueError:
        return None
    if pid > 0 and _pid_is_bridge_process(pid):
        return pid
    BRIDGE_PID_FILE.unlink(missing_ok=True)
    return None


def bridge_running() -> bool:
    if _read_bridge_pid() is not None:
        return True
    return is_port_open("127.0.0.1", BRIDGE_PORT) or is_port_open("0.0.0.0", BRIDGE_PORT)


def _pproxy_bridge_argv() -> list[str]:
    listen = f"socks5://0.0.0.0:{BRIDGE_PORT}"
    remote = f"socks5://127.0.0.1:{DEFAULT_TOR_PORT}"
    if getattr(sys, "frozen", False):
        worker = Path(sys.executable).resolve().parent / "pproxy-worker"
        if worker.is_file():
            return [str(worker), f"-l{listen}", f"-r{remote}"]
    return [sys.executable, "-m", "pproxy", f"-l{listen}", f"-r{remote}"]


def ensure_tor_bridge() -> tuple[bool, str]:
    """Garante Tor :9050 + bridge SOCKS acessível do Docker em :19050."""
    ok, msg = try_start_tor(DEFAULT_TOR_PORT)
    if not ok:
        return False, msg

    if bridge_running():
        return True, f"Bridge Tor OK — 0.0.0.0:{BRIDGE_PORT} → 127.0.0.1:{DEFAULT_TOR_PORT}"

    BRIDGE_PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    BRIDGE_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    try:
        log_f = open(BRIDGE_LOG_FILE, "ab")
        proc = subprocess.Popen(
            _pproxy_bridge_argv(),
            stdout=log_f,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        log_f.close()
    except OSError as exc:
        return False, f"Falha ao iniciar bridge Tor: {exc}"

    BRIDGE_PID_FILE.write_text(str(proc.pid), encoding="utf-8")
    for _ in range(20):
        time.sleep(0.15)
        if is_port_open("127.0.0.1", BRIDGE_PORT):
            return True, f"Bridge Tor em 0.0.0.0:{BRIDGE_PORT} → :{DEFAULT_TOR_PORT}"
        if proc.poll() is not None:
            break
    return False, (
        f"Bridge Tor não abriu a porta {BRIDGE_PORT}. "
        f"Veja {BRIDGE_LOG_FILE}"
    )


def stop_tor_bridge() -> None:
    pid = _read_bridge_pid()
    if pid is not None:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
        time.sleep(0.2)
        if Path(f"/proc/{pid}").exists():
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
    BRIDGE_PID_FILE.unlink(missing_ok=True)


def stop_tor_bridge_if_unused(apps: list[AppRule]) -> None:
    if any(is_elements_app(a) and (a.use_proxy or conf_has_proxy(a.id)) for a in apps):
        return
    stop_tor_bridge()


def _managed_block(proxy_endpoint: str) -> str:
    return (
        f"{_BEGIN}\n"
        f"# Gerenciado pelo Proxy Manager — Tor via bridge Docker\n"
        f"proxy={proxy_endpoint}\n"
        f"proxyrandomize=1\n"
        f"listenonion=0\n"
        f"{_END}\n"
    )


def _insert_block(text: str, block: str) -> str:
    cleaned = _BLOCK_RE.sub("", text)
    # Remove proxy= soltos antigos (não comentados) na seção global
    lines = cleaned.splitlines(keepends=True)
    out: list[str] = []
    in_network = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            in_network = True
        if not in_network and (
            stripped.startswith("proxy=")
            or stripped.startswith("proxyrandomize=")
            or stripped.startswith("listenonion=")
        ):
            continue
        out.append(line)
    cleaned = "".join(out)

    match = re.search(r"(?m)^\[[^\]]+\]\s*$", cleaned)
    if match:
        idx = match.start()
        return cleaned[:idx].rstrip() + "\n\n" + block + "\n" + cleaned[idx:].lstrip()
    return cleaned.rstrip() + "\n\n" + block


def _remove_block(text: str) -> str:
    cleaned = _BLOCK_RE.sub("", text)
    # limpa linhas em branco triplas
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned


def write_conf_proxy(app_id: str, *, enable: bool, proxy_endpoint: str | None = None) -> tuple[bool, str]:
    path = conf_path(app_id)
    if path is None:
        return False, f"App Elements desconhecido: {app_id}"
    if not path.is_file():
        return False, f"Conf não encontrado: {path}"

    try:
        original = path.read_text(encoding="utf-8")
    except OSError as exc:
        return False, f"Não foi possível ler {path}: {exc}"

    if enable:
        endpoint = proxy_endpoint or f"{docker_bridge_gateway()}:{BRIDGE_PORT}"
        new_text = _insert_block(original, _managed_block(endpoint))
    else:
        new_text = _remove_block(original)

    if new_text == original:
        return True, "Conf já estava no estado desejado."

    try:
        path.write_text(new_text, encoding="utf-8")
    except OSError as exc:
        return False, f"Não foi possível gravar {path}: {exc}"
    return True, f"Conf atualizado: {path.name}"


def container_running(container_name: str) -> bool:
    try:
        result = subprocess.run(
            ["docker", "inspect", "-f", "{{.State.Running}}", container_name],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
        return result.returncode == 0 and result.stdout.strip().lower() == "true"
    except (OSError, subprocess.TimeoutExpired):
        return False


def recreate_elements_service(app_id: str) -> tuple[bool, str]:
    target = TARGETS.get(app_id)
    if not target:
        return False, f"App desconhecido: {app_id}"
    if not LIQUID_COMPOSE_DIR.is_dir():
        return False, f"Compose não encontrado: {LIQUID_COMPOSE_DIR}"

    cmd = ["docker", "compose"]
    if target.compose_profile:
        cmd.extend(["--profile", target.compose_profile])
    cmd.extend(["up", "-d", "--force-recreate", target.compose_service])

    try:
        result = subprocess.run(
            cmd,
            cwd=str(LIQUID_COMPOSE_DIR),
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"Falha ao recriar {target.compose_service}: {exc}"

    if result.returncode != 0:
        err = (result.stderr or result.stdout or "").strip()[-400:]
        return False, f"docker compose falhou ({target.compose_service}): {err}"
    return True, f"Container {target.container_name} recriado."


def apply_elements_proxy(app: AppRule, *, use_proxy: bool) -> tuple[bool, str]:
    """Liga/desliga Tor no conf do Elements; recreia container se estiver rodando."""
    if not is_elements_app(app):
        return False, "Não é um app Elements/Liquid."

    target = TARGETS[app.id]
    was_running = container_running(target.container_name)

    if use_proxy:
        ok, msg = ensure_tor_bridge()
        if not ok:
            return False, msg
        gw = docker_bridge_gateway()
        # Sanity: bridge deve aceitar conexão do host (equivale ao gateway Docker)
        if not is_port_open("127.0.0.1", BRIDGE_PORT):
            return False, f"Bridge Tor não escuta em :{BRIDGE_PORT}."
        ok, conf_msg = write_conf_proxy(
            app.id, enable=True, proxy_endpoint=f"{gw}:{BRIDGE_PORT}"
        )
        if not ok:
            return False, conf_msg
    else:
        ok, conf_msg = write_conf_proxy(app.id, enable=False)
        if not ok:
            return False, conf_msg

    app.use_proxy = use_proxy
    app.enabled = use_proxy

    if was_running:
        ok, recreate_msg = recreate_elements_service(app.id)
        if not ok:
            return False, f"{conf_msg} Recreate falhou: {recreate_msg}"
        state = "via Tor" if use_proxy else "sem Tor"
        return True, f"{target.name}: conf OK, container recriado ({state})."

    state = "Tor no conf — suba o nó para aplicar" if use_proxy else "Tor removido do conf"
    return True, f"{target.name}: {state}."


def elements_status_label(app: AppRule) -> tuple[str, str]:
    """Texto/cor para o card (independente do gost :7890)."""
    target = TARGETS.get(app.id)
    if not target:
        return "○ Parado", "#64748b"

    has_proxy = conf_has_proxy(app.id)
    running = container_running(target.container_name)
    bridge_ok = bridge_running()
    tor_ok = tor_status(DEFAULT_TOR_PORT)[0]

    if has_proxy and running:
        if tor_ok and bridge_ok:
            return f"● Tor ativo\n{target.container_name}", "#f97316"
        return "◐ Tor no conf\n(bridge/Tor off)", "#fbbf24"
    if has_proxy and not running:
        return "◐ Tor no conf\n(nó parado)", "#fbbf24"
    if running:
        return "○ Rodando sem Tor", "#94a3b8"
    return "○ Parado", "#64748b"


# ── Watchdog do bridge ───────────────────────────────────────────────────────
#
# ensure_tor_bridge() só era chamado quando o usuário clicava no toggle da GUI.
# Se o processo do bridge morresse depois (crash, OOM, reboot do host), ele
# nunca mais voltava sozinho: o conf continuava apontando pra 172.19.0.1:19050
# indefinidamente e o elementsd ficava martelando connect() refused sem parar.
# Este watchdog reafirma o bridge periodicamente enquanto houver algum app
# Elements com Tor configurado no conf.

_WATCHDOG_INTERVAL = 20.0
_watchdog_stop = threading.Event()
_watchdog_thread: threading.Thread | None = None
_watchdog_apps_fn: Callable[[], list[AppRule]] | None = None
_watchdog_cb: Callable[[bool, str], None] | None = None


def start_bridge_watchdog(
    apps_fn: Callable[[], list[AppRule]],
    on_event: Callable[[bool, str], None] | None = None,
) -> None:
    """Inicia thread que monitora o bridge Tor do Elements e reinicia se cair.

    `apps_fn` é chamada a cada tick para pegar a lista de apps atual (não uma
    cópia congelada), já que `ConfigStore.apps` pode ser reatribuída em runtime.
    """
    global _watchdog_thread, _watchdog_stop, _watchdog_apps_fn, _watchdog_cb
    stop_bridge_watchdog()
    _watchdog_apps_fn = apps_fn
    _watchdog_cb = on_event
    _watchdog_stop = threading.Event()
    _watchdog_thread = threading.Thread(
        target=_bridge_watchdog_loop, daemon=True, name="elements-bridge-watchdog"
    )
    _watchdog_thread.start()


def stop_bridge_watchdog() -> None:
    global _watchdog_thread
    _watchdog_stop.set()
    if _watchdog_thread and _watchdog_thread.is_alive():
        _watchdog_thread.join(timeout=2.0)
    _watchdog_thread = None
    _watchdog_stop.clear()


def _bridge_watchdog_loop() -> None:
    svc.update(
        svc.SVC_ELEMENTS_BRIDGE, "ok", f"Watchdog ativo (intervalo {int(_WATCHDOG_INTERVAL)}s)"
    )
    while not _watchdog_stop.wait(timeout=_WATCHDOG_INTERVAL):
        apps_fn = _watchdog_apps_fn
        if apps_fn is None:
            continue
        try:
            apps = apps_fn()
        except Exception:
            continue

        configured = any_target_proxy_configured() or any(
            is_elements_app(a) and a.use_proxy for a in apps
        )
        if not configured:
            svc.log_only(svc.SVC_ELEMENTS_BRIDGE, "Nenhum app Elements com Tor ativo — ocioso.")
            continue

        if elements_bridge_healthy():
            svc.log_only(svc.SVC_ELEMENTS_BRIDGE, "check OK")
            continue

        svc.update(svc.SVC_ELEMENTS_BRIDGE, "aviso", "Bridge Tor caiu — reiniciando…")
        ok, msg = ensure_tor_bridge()
        if ok:
            svc.update(svc.SVC_ELEMENTS_BRIDGE, "ok", f"Reiniciado: {msg}")
        else:
            svc.update(svc.SVC_ELEMENTS_BRIDGE, "erro", f"Falha ao reiniciar: {msg}")
        if _watchdog_cb:
            _watchdog_cb(ok, msg)
    svc.update(svc.SVC_ELEMENTS_BRIDGE, "parado", "Watchdog encerrado.")
