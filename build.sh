#!/usr/bin/env bash
# build.sh — compila Proxy Manager em executável e cria atalho no desktop
#
# Versão (na ordem):
#   ./build.sh 0.01.004
#   ./build.sh --version 0.01.004
#   VERSION=0.01.004 ./build.sh
#   última tag git (git describe --tags --abbrev=0)
set -euo pipefail

cd "$(dirname "$0")"
ROOT="$(pwd)"

resolve_build_version() {
    if [[ -n "${VERSION:-}" ]]; then
        echo "$VERSION"
        return
    fi
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --version|-v)
                [[ $# -ge 2 ]] || { echo "ERRO: --version requer um valor" >&2; exit 1; }
                echo "$2"
                return
                ;;
            --version=*)
                echo "${1#*=}"
                return
                ;;
            -h|--help)
                sed -n '2,8p' "$0" | sed 's/^# \{0,1\}//'
                exit 0
                ;;
            --*)
                shift
                ;;
            *)
                echo "$1"
                return
                ;;
        esac
        shift
    done
    if git describe --tags --abbrev=0 &>/dev/null; then
        git describe --tags --abbrev=0
        return
    fi
    if git describe --tags --always &>/dev/null; then
        git describe --tags --always
        return
    fi
    echo "dev"
}

configure_version() {
    local ver="$1"
    python3 - "$ver" <<'PY'
import re
import sys
from pathlib import Path

version = sys.argv[1]
root = Path("proxy_manager")
(root / "_version.txt").write_text(version + "\n", encoding="utf-8")
init_py = root / "__init__.py"
text = init_py.read_text(encoding="utf-8")
text, n = re.subn(
    r'__version__\s*=\s*["\'][^"\']*["\']',
    f'__version__ = "{version}"',
    text,
    count=1,
)
if n == 0:
    raise SystemExit("ERRO: __version__ não encontrado em proxy_manager/__init__.py")
init_py.write_text(text, encoding="utf-8")
print(f"   versão configurada: {version}")
PY
}

COMMIT_HASH=""

configure_commit_hash() {
    local sha
    sha="$(git rev-parse HEAD 2>/dev/null || true)"
    if [[ -n "$sha" ]]; then
        COMMIT_HASH="${sha: -6}"
        echo "$COMMIT_HASH" > proxy_manager/_commit.txt
        echo "   hash configurado: $COMMIT_HASH"
    fi
}

APP_VERSION="$(resolve_build_version "$@")"

DIST="$ROOT/dist/proxy-manager"
EXE="$DIST/proxy-manager"
DESKTOP_DIR="$(xdg-user-dir DESKTOP 2>/dev/null || echo "$HOME/Desktop")"
DESKTOP_FILE="$DESKTOP_DIR/proxy-manager.desktop"
APPS_DESKTOP="$HOME/.local/share/applications/proxy-manager.desktop"
ICON_SRC="$ROOT/assets/icon.png"

echo "==> Configurando versão..."
configure_version "$APP_VERSION"
configure_commit_hash

echo "==> Ativando ambiente virtual..."
if [[ -d .venv ]]; then
    source .venv/bin/activate
else
    echo "ERRO: .venv não encontrado. Crie com: python3 -m venv .venv && pip install -r requirements.txt"
    exit 1
fi

echo "==> Verificando PyInstaller..."
if ! python -m PyInstaller --version &>/dev/null; then
    echo "Instalando PyInstaller..."
    pip install pyinstaller --quiet
fi

echo "==> Gerando ícone..."
python3 - <<'PYEOF'
from proxy_manager.brand_icon import make_brand_icon
from pathlib import Path
assets = Path("assets")
assets.mkdir(exist_ok=True)
img = make_brand_icon(256, proxy_on=True)
img.save(str(assets / "icon.png"))
print("   icon.png gerado (256x256)")
PYEOF

echo "==> Compilando com PyInstaller..."
python -m PyInstaller proxy-manager.spec \
    --noconfirm \
    --clean \
    2>&1 | grep -E "^(INFO|WARNING|ERROR|Building|Appending|Copying|\s*(.*\.py|ERROR))" || true

if [[ ! -f "$EXE" ]]; then
    echo "ERRO: executável não encontrado em $EXE"
    echo "Verifique os logs acima."
    exit 1
fi

WORKER="$DIST/pproxy-worker"
if [[ ! -f "$WORKER" ]]; then
    echo "ERRO: pproxy-worker não encontrado em $WORKER"
    exit 1
fi

echo "==> Executável gerado: $EXE"
echo "    Tamanho: $(du -sh "$DIST" | cut -f1)"

# ── Atalho no Desktop ──────────────────────────────────────────────────────

echo "==> Instalando ícones e atalhos..."

python3 - <<PYEOF
from pathlib import Path
from proxy_manager.brand_icon import make_brand_icon

icons_base = Path.home() / ".local/share/icons/hicolor"
for size in (16, 32, 48, 64, 128, 256):
    icon_dir = icons_base / f"{size}x{size}/apps"
    icon_dir.mkdir(parents=True, exist_ok=True)
    make_brand_icon(size, proxy_on=True).save(str(icon_dir / "proxy-manager.png"))
print("   ícones instalados (16–256px)")
PYEOF

DESKTOP_CONTENT="[Desktop Entry]
Name=Proxy Manager
Comment=Gerenciador de proxy por aplicativo ($APP_VERSION)
Exec=$EXE
Icon=proxy-manager
Type=Application
Categories=Network;Settings;
StartupWMClass=proxy-manager
StartupNotify=true
Terminal=false
"

printf '%s' "$DESKTOP_CONTENT" > "$DESKTOP_FILE"
mkdir -p "$(dirname "$APPS_DESKTOP")"
printf '%s' "$DESKTOP_CONTENT" > "$APPS_DESKTOP"
chmod +x "$DESKTOP_FILE" "$APPS_DESKTOP"

# Marca como confiável no GNOME (evita o diálogo "arquivo não confiável")
if command -v gio &>/dev/null; then
    gio set "$DESKTOP_FILE" metadata::trusted true 2>/dev/null || true
fi

# Atualiza caches
update-desktop-database "$HOME/.local/share/applications/" 2>/dev/null || true
gtk-update-icon-cache -f -t "$HOME/.local/share/icons/hicolor" 2>/dev/null || true

echo ""
if [[ -n "$COMMIT_HASH" ]]; then
    echo "✓ Build completo!  v$APP_VERSION ($COMMIT_HASH)"
else
    echo "✓ Build completo!  v$APP_VERSION"
fi
echo "  Executável : $EXE"
echo "  Atalho     : $DESKTOP_FILE"
echo "  Menu apps  : $APPS_DESKTOP"
echo ""
echo "  Para executar direto:"
echo "    $EXE"
