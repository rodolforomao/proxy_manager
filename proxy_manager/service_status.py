"""Registro central thread-safe de status de serviços em background."""
from __future__ import annotations

import threading
from collections import deque
from datetime import datetime
from typing import Literal

ServiceState = Literal["ok", "rodando", "aviso", "erro", "parado", "desconhecido"]

STATE_COLORS: dict[ServiceState, str] = {
    "ok":          "#4ade80",
    "rodando":     "#60a5fa",
    "aviso":       "#facc15",
    "erro":        "#f87171",
    "parado":      "#94a3b8",
    "desconhecido":"#64748b",
}

STATE_LABELS: dict[ServiceState, str] = {
    "ok":          "OK",
    "rodando":     "Rodando",
    "aviso":       "Aviso",
    "erro":        "Erro",
    "parado":      "Parado",
    "desconhecido":"—",
}

_MAX_LOG = 200   # linhas no log global
_MAX_SVC = 30    # linhas no log por serviço


class ServiceEntry:
    def __init__(self, service_id: str, name: str) -> None:
        self.service_id = service_id
        self.name = name
        self.state: ServiceState = "desconhecido"
        self.detail: str = ""
        self.last_updated: datetime | None = None
        self.log: deque[str] = deque(maxlen=_MAX_SVC)

    def _record(self, state: ServiceState, detail: str) -> None:
        now = datetime.now()
        self.state = state
        self.detail = detail
        self.last_updated = now
        ts = now.strftime("%H:%M:%S")
        label = STATE_LABELS.get(state, state)
        self.log.appendleft(f"[{ts}] [{label}] {detail}" if detail else f"[{ts}] [{label}]")


class ServiceRegistry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._services: dict[str, ServiceEntry] = {}
        self.global_log: deque[str] = deque(maxlen=_MAX_LOG)
        self._listeners: list = []

    # ── registo ────────────────────────────────────────────────────────────

    def register(self, service_id: str, name: str) -> None:
        with self._lock:
            if service_id not in self._services:
                self._services[service_id] = ServiceEntry(service_id, name)

    # ── atualização ────────────────────────────────────────────────────────

    def update(self, service_id: str, state: ServiceState, detail: str = "") -> None:
        with self._lock:
            if service_id not in self._services:
                self._services[service_id] = ServiceEntry(service_id, service_id)
            svc = self._services[service_id]
            svc._record(state, detail)
            ts = datetime.now().strftime("%H:%M:%S")
            label = STATE_LABELS.get(state, state)
            line = f"[{ts}] {svc.name}: [{label}] {detail}" if detail else f"[{ts}] {svc.name}: [{label}]"
            self.global_log.appendleft(line)
        self._notify()

    def log_only(self, service_id: str, detail: str) -> None:
        """Registra linha no log sem alterar o estado do serviço."""
        with self._lock:
            if service_id not in self._services:
                self._services[service_id] = ServiceEntry(service_id, service_id)
            svc = self._services[service_id]
            ts = datetime.now().strftime("%H:%M:%S")
            svc.log.appendleft(f"[{ts}] {detail}")
            self.global_log.appendleft(f"[{ts}] {svc.name}: {detail}")
        self._notify()

    # ── leitura ────────────────────────────────────────────────────────────

    def snapshot(self) -> list[ServiceEntry]:
        """Retorna cópia rasa da lista de serviços (thread-safe)."""
        with self._lock:
            return list(self._services.values())

    def global_log_lines(self, n: int = 100) -> list[str]:
        with self._lock:
            return list(self.global_log)[:n]

    def svc_log_lines(self, service_id: str, n: int = 30) -> list[str]:
        with self._lock:
            svc = self._services.get(service_id)
            if svc is None:
                return []
            return list(svc.log)[:n]

    # ── listeners (GUI) ────────────────────────────────────────────────────

    def add_listener(self, fn) -> None:
        with self._lock:
            self._listeners.append(fn)

    def remove_listener(self, fn) -> None:
        with self._lock:
            self._listeners = [f for f in self._listeners if f is not fn]

    def _notify(self) -> None:
        with self._lock:
            listeners = list(self._listeners)
        for fn in listeners:
            try:
                fn()
            except Exception:
                pass


# ── instância global ────────────────────────────────────────────────────────

_registry = ServiceRegistry()


def register(service_id: str, name: str) -> None:
    _registry.register(service_id, name)


def update(service_id: str, state: ServiceState, detail: str = "") -> None:
    _registry.update(service_id, state, detail)


def log_only(service_id: str, detail: str) -> None:
    _registry.log_only(service_id, detail)


def snapshot() -> list[ServiceEntry]:
    return _registry.snapshot()


def global_log_lines(n: int = 100) -> list[str]:
    return _registry.global_log_lines(n)


def svc_log_lines(service_id: str, n: int = 30) -> list[str]:
    return _registry.svc_log_lines(service_id, n)


def add_listener(fn) -> None:
    _registry.add_listener(fn)


def remove_listener(fn) -> None:
    _registry.remove_listener(fn)


# IDs canônicos dos serviços
SVC_GOST       = "gost"
SVC_WATCHDOG   = "watchdog"
SVC_AUTOCONFIG = "autoconfig"
SVC_SCANNER    = "scanner"
SVC_TOR        = "tor"
SVC_IFACE      = "iface_refresh"
SVC_TRAY       = "tray"
SVC_SSH_SOCKS  = "ssh_socks"

# Pré-registra os serviços conhecidos
register(SVC_GOST,       "Proxy local (gost)")
register(SVC_WATCHDOG,   "Watchdog")
register(SVC_AUTOCONFIG, "Auto-config")
register(SVC_SCANNER,    "Scanner de processos")
register(SVC_TOR,        "Tor")
register(SVC_IFACE,      "Refresh de interfaces")
register(SVC_TRAY,       "Bandeja do sistema")
register(SVC_SSH_SOCKS,  "Túnel SOCKS5 (SSH)")
register("ui",           "Interface")
