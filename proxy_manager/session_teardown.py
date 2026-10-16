"""Desfaz o proxy no fechamento da janela e antes do desligamento desmontar o disco.

O mainloop do Tk não observa o wakeup de sinal do Python. No poweroff o
SIGTERM chega, a limpeza era adiada com after(), e o processo morria por
SIGKILL com Firefox, Claude e Elements ainda apontando para o proxy.
"""
from __future__ import annotations

import atexit
import os
import shutil
import signal
import subprocess
import threading
from typing import Callable

Cleanup = Callable[[], None]
_holds: list[subprocess.Popen] = []
_holds_lock = threading.Lock()


def prepare_for_shutdown_is_stopping(line: str) -> bool:
    """True só quando o logind avisa que o desligamento começou."""
    lowered = line.lower()
    return "prepareforshutdown" in lowered and "true" in lowered


def arm_session_teardown(root, cleanup: Cleanup) -> None:
    """Acorda o Tk no SIGTERM e escuta o PrepareForShutdown do logind."""
    read_fd, write_fd = os.pipe()
    os.set_blocking(read_fd, False)
    os.set_blocking(write_fd, False)
    # Qualquer sinal acorda o select do Tcl; o handler Python roda em seguida.
    signal.set_wakeup_fd(write_fd)

    quit_read, quit_write = os.pipe()
    os.set_blocking(quit_read, False)
    os.set_blocking(quit_write, False)

    def _drain(fd: int) -> None:
        try:
            while True:
                chunk = os.read(fd, 256)
                if not chunk:
                    break
        except BlockingIOError:
            pass

    def _finish() -> None:
        try:
            cleanup()
        finally:
            release_session_holds()
            try:
                root.quit()
            except Exception:
                pass

    def _on_signal_wake(_fd, _mask) -> None:
        _drain(read_fd)

    def _on_quit_wake(_fd, _mask) -> None:
        _drain(quit_read)
        _finish()

    def _handle_stop(_signum=None, _frame=None) -> None:
        _finish()

    try:
        import tkinter

        root.tk.createfilehandler(read_fd, tkinter.READABLE, _on_signal_wake)
        root.tk.createfilehandler(quit_read, tkinter.READABLE, _on_quit_wake)
    except Exception:
        pass

    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        try:
            signal.signal(sig, _handle_stop)
        except (ValueError, OSError):
            pass

    atexit.register(release_session_holds)
    _arm_logind_teardown(quit_write, cleanup)


def _arm_logind_teardown(quit_write: int, cleanup: Cleanup) -> None:
    """Segura o shutdown por alguns segundos e limpa quando o logind avisa."""
    _track(_start_shutdown_inhibitor())
    if _start_prepare_watcher(quit_write, cleanup) is None:
        release_session_holds()


def _start_shutdown_inhibitor() -> subprocess.Popen | None:
    binary = shutil.which("systemd-inhibit")
    if not binary:
        return None
    try:
        return subprocess.Popen(
            [
                binary,
                "--what=shutdown",
                "--mode=delay",
                "--who=Proxy Manager",
                "--why=Desfazer configurações de proxy antes de desligar",
                "sleep",
                "infinity",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError:
        return None


def _start_prepare_watcher(quit_write: int, cleanup: Cleanup) -> threading.Thread | None:
    gdbus = shutil.which("gdbus")
    if not gdbus:
        return None

    def _watch() -> None:
        proc: subprocess.Popen | None = None
        try:
            proc = subprocess.Popen(
                [
                    gdbus,
                    "monitor",
                    "--system",
                    "--dest",
                    "org.freedesktop.login1",
                    "--object-path",
                    "/org/freedesktop/login1",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
            )
            _track(proc)
            assert proc.stdout is not None
            for line in proc.stdout:
                if not prepare_for_shutdown_is_stopping(line):
                    continue
                try:
                    cleanup()
                finally:
                    release_session_holds()
                try:
                    os.write(quit_write, b"\0")
                except OSError:
                    pass
                break
        except Exception:
            pass
        finally:
            _stop_process(proc)

    thread = threading.Thread(target=_watch, name="session-teardown", daemon=True)
    thread.start()
    return thread


def _track(proc: subprocess.Popen | None) -> subprocess.Popen | None:
    if proc is None:
        return None
    with _holds_lock:
        _holds.append(proc)
    return proc


def release_session_holds() -> None:
    """Solta o inibidor de shutdown e o monitor do logind."""
    with _holds_lock:
        procs = list(_holds)
        _holds.clear()
    for proc in procs:
        _stop_process(proc)


def _stop_process(proc: subprocess.Popen | None) -> None:
    if proc is None or proc.poll() is not None:
        return
    try:
        proc.terminate()
        proc.wait(timeout=1)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass
