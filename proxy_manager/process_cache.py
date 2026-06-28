from __future__ import annotations

import threading
import time
from typing import Callable

from proxy_manager.models import AppRule, ProcessInfo, ProxySettings
from proxy_manager.process_monitor import scan_processes

OnScanDone = Callable[[list[ProcessInfo]], None]


class ProcessScanner:
    def __init__(self) -> None:
        self._cache: list[ProcessInfo] = []
        self._cache_at: float = 0.0
        self._lock = threading.Lock()
        self._running = False

    @property
    def cache(self) -> list[ProcessInfo]:
        with self._lock:
            return list(self._cache)

    def by_app_id(self) -> dict[str, ProcessInfo]:
        result: dict[str, ProcessInfo] = {}
        for proc in self.cache:
            if proc.matched_app:
                result[proc.matched_app.id] = proc
        return result

    def refresh_async(
        self,
        apps: list[AppRule],
        proxy: ProxySettings,
        on_done: OnScanDone,
        *,
        detect_network: bool = False,
        min_interval: float = 4.0,
    ) -> None:
        now = time.monotonic()
        with self._lock:
            if self._running:
                return
            if now - self._cache_at < min_interval and self._cache:
                on_done(list(self._cache))
                return
            self._running = True

        def work() -> None:
            try:
                processes = scan_processes(apps, proxy, detect_network=detect_network)
            except Exception:
                processes = []
            with self._lock:
                self._cache = processes
                self._cache_at = time.monotonic()
                self._running = False
            on_done(processes)

        threading.Thread(target=work, daemon=True).start()

    def invalidate(self) -> None:
        with self._lock:
            self._cache_at = 0.0
