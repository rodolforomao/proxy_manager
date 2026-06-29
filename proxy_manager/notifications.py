from __future__ import annotations

import subprocess
import threading


def notify(title: str, body: str = "", urgency: str = "normal") -> None:
    """Envia notificação desktop via notify-send (não-bloqueante)."""
    def _send() -> None:
        try:
            subprocess.run(
                [
                    "notify-send",
                    "-u", urgency,
                    "-a", "Proxy Manager",
                    "--icon", "network-proxy",
                    title,
                    body,
                ],
                capture_output=True,
                timeout=3,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass

    threading.Thread(target=_send, daemon=True).start()


def notify_proxy_up(route: str) -> None:
    notify("Proxy ativo", route, urgency="normal")


def notify_proxy_down(reason: str = "") -> None:
    body = reason if reason else "Proxy local desligado."
    notify("Proxy desligado", body, urgency="normal")


def notify_proxy_error(reason: str) -> None:
    notify("Proxy com erro", reason, urgency="critical")


def notify_proxy_recovered(route: str) -> None:
    notify("Proxy recuperado", f"Reiniciado: {route}", urgency="normal")
