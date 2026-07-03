#!/usr/bin/env python3
"""Proxy Manager - GUI para controlar proxy por aplicativo."""

import atexit
import fcntl
import os
import signal
import subprocess
import sys
import time
import warnings
from pathlib import Path

# pproxy usa strings de escape inválidas em Python 3.12+ (ex: '\[')
warnings.filterwarnings("ignore", category=SyntaxWarning, module="pproxy")

from proxy_manager.gui import run

# Diretório raiz do projeto (onde este main.py está)
_PROJECT_DIR = Path(__file__).resolve().parent


def _install_desktop_integration() -> None:
    """Instala ícone e .desktop para aparecer no dock/taskbar do GNOME/KDE."""
    try:
        from proxy_manager.brand_icon import make_brand_icon

        # 1. Exporta ícone PNG em múltiplos tamanhos
        icons_base = Path.home() / ".local/share/icons/hicolor"
        for size in (16, 32, 48, 64, 128, 256):
            icon_dir = icons_base / f"{size}x{size}/apps"
            icon_dir.mkdir(parents=True, exist_ok=True)
            icon_path = icon_dir / "proxy-manager.png"
            img = make_brand_icon(size, proxy_on=True)
            img.save(str(icon_path))

        # 2. Escreve .desktop file
        desktop_dir = Path.home() / ".local/share/applications"
        desktop_dir.mkdir(parents=True, exist_ok=True)
        if getattr(sys, "frozen", False):
            exec_cmd = sys.executable
        else:
            python_bin = sys.executable
            run_script = _PROJECT_DIR / "run.sh"
            exec_cmd = str(run_script) if run_script.exists() else f"{python_bin} {_PROJECT_DIR / 'main.py'}"

        desktop_content = f"""[Desktop Entry]
Name=Proxy Manager
Comment=Gerenciador de proxy por aplicativo
Exec={exec_cmd}
Icon=proxy-manager
Type=Application
Categories=Network;Settings;
StartupWMClass=proxy-manager
StartupNotify=true
Terminal=false
"""
        desktop_file = desktop_dir / "proxy-manager.desktop"
        desktop_file.write_text(desktop_content, encoding="utf-8")
        desktop_file.chmod(0o755)

        # 3. Atualiza caches (não bloqueia)
        subprocess.Popen(
            ["update-desktop-database", str(desktop_dir)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        subprocess.Popen(
            ["gtk-update-icon-cache", "-f", "-t", str(icons_base)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass  # não crítico

LOCK_FILE = Path.home() / ".config" / "proxy-manager" / "instance.lock"
INSTANCE_SIGNAL = signal.SIGUSR1
_lock_handle = None


def _read_lock_pid() -> int | None:
    if not LOCK_FILE.exists():
        return None
    try:
        pid = int(LOCK_FILE.read_text(encoding="utf-8").strip())
    except ValueError:
        return None
    if pid <= 0 or not Path(f"/proc/{pid}").exists():
        return None
    try:
        cmdline = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return None
    if b"main.py" not in cmdline:
        return None  # PID reaproveitado por outro processo
    return pid


def _focus_existing_instance() -> bool:
    """Traz a janela já aberta para frente (segunda execução do atalho)."""
    pid = _read_lock_pid()
    if pid is None:
        return False
    try:
        os.kill(pid, INSTANCE_SIGNAL)
        return True
    except OSError:
        return False


def _prompt_duplicate_instance(pid: int) -> bool:
    """Pergunta o que fazer ao detectar outra instância rodando (PID `pid`).

    Retorna True se deve encerrar a existente e abrir uma nova.
    Padrão (Não / fechar a janela) é NÃO iniciar uma nova instância.
    """
    import tkinter as tk
    from tkinter import messagebox

    root = tk.Tk()
    root.withdraw()
    try:
        return messagebox.askyesno(
            "Proxy Manager já está em execução",
            f"Já existe uma instância em execução (PID {pid}).\n\n"
            "Encerrar a instância existente e abrir uma nova?\n"
            "Escolher 'Não' mantém a instância atual e cancela a abertura.",
            default=messagebox.NO,
        )
    finally:
        root.destroy()


def _kill_existing_instance(pid: int, timeout: float = 5.0) -> bool:
    """Encerra a instância `pid` e aguarda ela liberar o lock/processo."""
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return True  # já não existe

    deadline = time.time() + timeout
    while time.time() < deadline:
        if not Path(f"/proc/{pid}").exists():
            return True
        time.sleep(0.2)

    try:
        os.kill(pid, signal.SIGKILL)
    except OSError:
        pass
    time.sleep(0.3)
    return not Path(f"/proc/{pid}").exists()


def _acquire_single_instance() -> bool:
    """Impede duas GUIs simultâneas (causa travamentos e proxy inconsistente).

    Abre em "a+" (sem truncar) — abrir em "w" apagaria o PID de quem já
    detém o lock antes mesmo de sabermos se conseguimos adquiri-lo, o que
    quebrava a leitura do PID existente (e o foco/kill da instância antiga).
    """
    global _lock_handle
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    handle = open(LOCK_FILE, "a+", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return False
    handle.seek(0)
    handle.truncate()
    handle.write(str(os.getpid()))
    handle.flush()
    _lock_handle = handle
    return True


def _release_single_instance() -> None:
    """Libera o lock. Remove o arquivo ANTES de destravar/fechar: evita a janela
    onde um processo novo poderia criar um inode diferente e ambos pensarem
    que são a única instância (causa de instâncias duplicadas)."""
    global _lock_handle
    if _lock_handle is None:
        return
    try:
        LOCK_FILE.unlink(missing_ok=True)
    except OSError:
        pass
    try:
        fcntl.flock(_lock_handle.fileno(), fcntl.LOCK_UN)
        _lock_handle.close()
    except OSError:
        pass
    _lock_handle = None


def _shutdown(signum=None, frame=None) -> None:
    try:
        from proxy_manager.local_proxy import stop_watchdog, stop_local_proxy
        stop_watchdog()
        stop_local_proxy()
    except Exception:
        pass
    _release_single_instance()
    sys.exit(0)


def main() -> None:
    if not _acquire_single_instance():
        existing_pid = _read_lock_pid()
        if existing_pid is None:
            print(
                "Proxy Manager já está em execução (PID não identificado).\n"
                "Feche a outra instância antes de abrir de novo.",
                file=sys.stderr,
            )
            sys.exit(1)

        if _prompt_duplicate_instance(existing_pid):
            if not (_kill_existing_instance(existing_pid) and _acquire_single_instance()):
                print(
                    "Não foi possível encerrar a instância existente.",
                    file=sys.stderr,
                )
                sys.exit(1)
            # instância antiga encerrada — segue o fluxo normal abaixo
        else:
            _focus_existing_instance()
            sys.exit(0)

    # Instala ícone e .desktop na primeira execução (e a cada atualização)
    _install_desktop_integration()

    atexit.register(_shutdown)
    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)
    try:
        run()
    finally:
        _release_single_instance()


if __name__ == "__main__":
    main()
