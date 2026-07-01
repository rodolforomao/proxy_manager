#!/usr/bin/env python3
"""Proxy Manager - GUI para controlar proxy por aplicativo."""

import atexit
import fcntl
import os
import signal
import subprocess
import sys
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
        # Usa o python do venv atual (ou o sistema)
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
    if pid > 0 and Path(f"/proc/{pid}").exists():
        return pid
    return None


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


def _acquire_single_instance() -> bool:
    """Impede duas GUIs simultâneas (causa travamentos e proxy inconsistente)."""
    global _lock_handle
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    handle = open(LOCK_FILE, "w", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return False
    handle.write(str(__import__("os").getpid()))
    handle.flush()
    _lock_handle = handle
    return True


def _release_single_instance() -> None:
    global _lock_handle
    if _lock_handle is None:
        return
    try:
        fcntl.flock(_lock_handle.fileno(), fcntl.LOCK_UN)
        _lock_handle.close()
    except OSError:
        pass
    _lock_handle = None
    LOCK_FILE.unlink(missing_ok=True)


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
        if _focus_existing_instance():
            sys.exit(0)
        print(
            "Proxy Manager já está em execução.\n"
            "Feche a outra janela antes de abrir de novo.",
            file=sys.stderr,
        )
        sys.exit(1)

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
