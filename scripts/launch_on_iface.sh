#!/usr/bin/env bash
# Roteia o processo filho pela interface de rede escolhida (requer root via pkexec).
set -euo pipefail

IFACE="${1:?interface required}"
shift
CMD=("$@")

if [[ "$(id -u)" -ne 0 ]]; then
  export PM_LAUNCH_USER="${USER:-root}"
  exec pkexec "$(readlink -f "$0")" "$IFACE" "${CMD[@]}"
fi

RUN_USER="${SUDO_USER:-${PM_LAUNCH_USER:-root}}"
MARK=$((0x5000 + $(echo -n "$IFACE" | cksum | cut -d' ' -f1) % 0x0FFF))
TABLE=$((200 + $(echo -n "$IFACE" | cksum | cut -d' ' -f1) % 800))

GW="$(ip -4 route show dev "$IFACE" 2>/dev/null | awk '/default/ {print $3; exit}')"
if [[ -z "$GW" ]]; then
  GW="$(ip route get 1.1.1.1 oif "$IFACE" 2>/dev/null | awk '{for (i = 1; i <= NF; i++) if ($i == "via") {print $(i + 1); exit}}')"
fi

if [[ -n "$GW" ]]; then
  ip route replace default via "$GW" dev "$IFACE" table "$TABLE" 2>/dev/null || true
else
  ip route replace default dev "$IFACE" table "$TABLE" 2>/dev/null || true
fi

ip rule del fwmark "$MARK" table "$TABLE" 2>/dev/null || true
ip rule add fwmark "$MARK" lookup "$TABLE" priority "$TABLE" 2>/dev/null || true

CGROUP_PATH=""
for base in /sys/fs/cgroup /sys/fs/cgroup/unified; do
  if [[ -d "$base" ]]; then
    CGROUP_PATH="$base/proxy-manager-$$"
    mkdir -p "$CGROUP_PATH" 2>/dev/null || continue
    break
  fi
done

run_app() {
  if command -v runuser >/dev/null 2>&1; then
    exec runuser -u "$RUN_USER" -- "${CMD[@]}"
  else
    exec sudo -u "$RUN_USER" -- "${CMD[@]}"
  fi
}

if [[ -n "$CGROUP_PATH" && -f "$CGROUP_PATH/cgroup.procs" ]]; then
  CGROUP_ID="$(stat -c '%g' "$CGROUP_PATH")"

  if command -v nft >/dev/null 2>&1; then
    nft list table inet proxy_manager >/dev/null 2>&1 || nft add table inet proxy_manager
    nft list chain inet proxy_manager output >/dev/null 2>&1 || \
      nft add chain inet proxy_manager output '{ type route hook output priority mangle; policy accept; }'
    nft add rule inet proxy_manager output meta cgroup "$CGROUP_ID" meta mark set "$MARK" 2>/dev/null || true
  elif command -v iptables >/dev/null 2>&1; then
    iptables -t mangle -A OUTPUT -m cgroup --cgroup "$CGROUP_ID" -j MARK --set-mark "$MARK" 2>/dev/null || true
  fi

  if command -v runuser >/dev/null 2>&1; then
    runuser -u "$RUN_USER" -- "${CMD[@]}" &
  else
    sudo -u "$RUN_USER" -- "${CMD[@]}" &
  fi
  CHILD=$!
  echo "$CHILD" >"$CGROUP_PATH/cgroup.procs" 2>/dev/null || true
  wait "$CHILD"
  exit $?
fi

run_app
