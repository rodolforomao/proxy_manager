#!/usr/bin/env bash
# Instala regras polkit para permitir roteamento por interface sem senha (opcional).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LAUNCH_SCRIPT="$SCRIPT_DIR/launch_on_iface.sh"
POLKIT_DIR="/etc/polkit-1/rules.d"
RULE_FILE="$POLKIT_DIR/49-proxy-manager-network.rules"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Execute com sudo: sudo $0"
  exit 1
fi

chmod +x "$LAUNCH_SCRIPT"

cat >"$RULE_FILE" <<EOF
polkit.addRule(function(action, subject) {
    if (action.id == "org.freedesktop.policykit.exec" &&
        action.lookup("program") == "$LAUNCH_SCRIPT") {
        if (subject.isInGroup("sudo") || subject.user == "root") {
            return polkit.Result.YES;
        }
    }
});
EOF

echo "Polkit configurado em $RULE_FILE"
echo "Interfaces detectadas:"
ip -o link show | awk -F': ' '{print "  - " $2}'
echo ""
echo "Pronto. Agora você pode escolher Ethernet/Wi-Fi por app na GUI."
