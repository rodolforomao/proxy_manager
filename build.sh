#!/usr/bin/env bash
# build.sh — compila Proxy Manager em executável e cria atalho no desktop
set -euo pipefail

cd "$(dirname "$0")"
ROOT="$(pwd)"
DIST="$ROOT/dist/proxy-manager"
EXE="$DIST/proxy-manager"
DESKTOP_DIR="$(xdg-user-dir DESKTOP 2>/dev/null || echo "$HOME/Desktop")"
DESKTOP_FILE="$DESKTOP_DIR/proxy-manager.desktop"
APPS_DESKTOP="$HOME/.local/share/applications/proxy-manager.desktop"
ICON_SRC="$ROOT/assets/icon.png"

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
VERSION="$(git describe --tags --abbrev=0 2>/dev/null || echo dev)"
echo "$VERSION" > proxy_manager/_version.txt
echo "   versão: $VERSION"
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
Comment=Gerenciador de proxy por aplicativo
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
echo "✓ Build completo!"
echo "  Executável : $EXE"
echo "  Atalho     : $DESKTOP_FILE"
echo "  Menu apps  : $APPS_DESKTOP"
echo ""
echo "  Para executar direto:"
echo "    $EXE"
