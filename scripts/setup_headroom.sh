#!/usr/bin/env bash
# Set up Headroom (token-usage compression) for ClipForge + Cline.
#
# Headroom (https://github.com/headroomlabs-ai/headroom) compresses tool
# output, logs, JSON and search results before they reach the model. It runs
# fully locally: the proxy and the kompress model never leave the machine.
#
# Usage:
#   ./scripts/setup_headroom.sh          # install + configure + start proxy
#   ./scripts/setup_headroom.sh start    # (re)start the proxy only
#   ./scripts/setup_headroom.sh measure  # measure savings on this repo
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="$REPO_ROOT/venv"
PROXY_PORT="${HEADROOM_PORT:-8787}"
PROXY_URL="http://127.0.0.1:$PROXY_PORT"
CLINE_MCP="$HOME/.cline/data/settings/cline_mcp_settings.json"

export HEADROOM_OFFLINE=1      # never phone home
export HEADROOM_BEACON=off
export DO_NOT_TRACK=1

install() {
  echo "==> Installing headroom-ai (proxy + code extras) and faster-whisper"
  "$VENV/bin/pip" install 'headroom-ai[proxy,code]' faster-whisper
}

configure_mcp() {
  echo "==> Registering the Headroom MCP server for Cline ($CLINE_MCP)"
  mkdir -p "$(dirname "$CLINE_MCP")"
  if [ ! -f "$CLINE_MCP" ]; then
    echo '{"mcpServers": {}}' > "$CLINE_MCP"
  fi
  "$VENV/bin/python" - "$CLINE_MCP" "$VENV" "$PROXY_URL" <<'PY'
import json, sys
path, venv, proxy = sys.argv[1], sys.argv[2], sys.argv[3]
try:
    with open(path) as fh:
        cfg = json.load(fh)
except Exception:
    cfg = {"mcpServers": {}}
cfg.setdefault("mcpServers", {})["headroom"] = {
    "command": f"{venv}/bin/headroom",
    "args": ["mcp", "serve"],
    "env": {
        "HEADROOM_PROXY_URL": proxy,
        "HEADROOM_OFFLINE": "1",
        "HEADROOM_BEACON": "off",
        "DO_NOT_TRACK": "1",
    },
    "disabled": False,
    "autoApprove": [],
}
with open(path, "w") as fh:
    json.dump(cfg, fh, indent=2)
    fh.write("\n")
print("wrote", path)
PY
}

start_proxy() {
  echo "==> Starting the Headroom proxy on $PROXY_URL"
  pkill -f 'headroom proxy' 2>/dev/null || true
  sleep 2
  nohup "$VENV/bin/headroom" proxy --port "$PROXY_PORT" --code-aware \
    > /tmp/headroom-proxy.log 2>&1 &
  for _ in $(seq 1 30); do
    if curl -sf "$PROXY_URL/health" > /dev/null 2>&1; then
      echo "proxy healthy: $PROXY_URL"
      return 0
    fi
    sleep 2
  done
  echo "proxy failed to start; see /tmp/headroom-proxy.log" >&2
  return 1
}

measure() {
  echo "==> Measuring Headroom savings on this repository"
  "$VENV/bin/python" "$REPO_ROOT/scripts/measure_headroom_savings.py"
}

case "${1:-setup}" in
  setup)   install; configure_mcp; start_proxy; measure ;;
  install) install ;;
  mcp)     configure_mcp ;;
  start)   start_proxy ;;
  measure) measure ;;
  *) echo "usage: $0 [setup|install|mcp|start|measure]" >&2; exit 2 ;;
esac
