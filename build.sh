#!/usr/bin/env bash
# build.sh — compila Proxy Manager em executável e cria atalho no desktop
set -euo pipefail

cd "$(dirname "$0")"
ROOT="$(pwd)"
DIST="$ROOT/dist/proxy-manager"
EXE="$DIST/proxy-manager"
DESKTOP_FILE="$HOME/Desktop/proxy-manager.desktop"
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

echo "==> Criando atalho no desktop..."

# Copia ícone para local padrão
ICON_DEST="$HOME/.local/share/icons/hicolor/256x256/apps/proxy-manager.png"
mkdir -p "$(dirname "$ICON_DEST")"
cp "$ICON_SRC" "$ICON_DEST"

cat > "$DESKTOP_FILE" <<DESKTOP
[Desktop Entry]
Name=Proxy Manager
Comment=Gerenciador de proxy por aplicativo
Exec=$EXE
Icon=proxy-manager
Type=Application
Categories=Network;Settings;
StartupWMClass=proxy-manager
StartupNotify=true
Terminal=false
DESKTOP

chmod +x "$DESKTOP_FILE"

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
echo ""
echo "  Para executar direto:"
echo "    $EXE"
