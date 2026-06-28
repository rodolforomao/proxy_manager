#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

echo "==> Ambiente Python"
if [[ ! -d .venv ]]; then
  python3 -m venv .venv
fi
source .venv/bin/activate
pip install -q -r requirements.txt

echo "==> Proxy local (pproxy + gost opcional)"
python3 -c "
from proxy_manager.local_proxy import install_gost
import pproxy

ok, msg = install_gost()
if ok:
    print(f'gost: {msg}')
else:
    print(f'gost não instalado ({msg}) — usando pproxy do venv.')
print(f'pproxy: ok ({pproxy.__file__})')
"

echo ""
echo "Pronto! Execute: ./run.sh"
echo "Configure o proxy na aba Configurações e use PROXY LIGADO."
