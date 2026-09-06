#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_USER="pi"
APP_DIR="/home/pi/LiveStageAssistant"
PYTHON_BIN="$APP_DIR/.venv/bin/python"
ENV_DIR="/etc/livestageassistant"
ONLINE_ENV="$ENV_DIR/.env.online"
OFFLINE_ENV="$ENV_DIR/.env.offline"
PIPER_VOICE="fr_FR-siwis-medium"
PIPER_MODEL="$APP_DIR/data/piper/$PIPER_VOICE.onnx"
PIPER_CONFIG="$PIPER_MODEL.json"

require_python_stack() {
  if [ ! -x "$PYTHON_BIN" ]; then
    echo "Error: $PYTHON_BIN not found or not executable." >&2
    echo "Create the virtual environment first, then install LiveStageAssistant dependencies." >&2
    echo "Example: cd $APP_DIR && ./scripts/install.sh" >&2
    exit 1
  fi

  if ! "$PYTHON_BIN" - <<'PY'
import sys
if sys.version_info < (3, 11):
    raise SystemExit(1)
PY
  then
    echo "Error: Live Stage Assistant requires Python >= 3.11." >&2
    exit 1
  fi

  if ! "$PYTHON_BIN" - <<'PY'
from importlib import metadata
metadata.version("openwakeword")
PY
  then
    echo "Warning: openWakeWord is not installed in $APP_DIR/.venv." >&2
    echo "Run: cd $APP_DIR && ./scripts/install.sh" >&2
    echo "Or manually: $PYTHON_BIN -m pip install -e '.[wakeword]'" >&2
  fi

  if ! "$PYTHON_BIN" - <<'PY'
from importlib import metadata
metadata.version("piper-tts")
import voice_assistant.realtime
PY
  then
    echo "Error: Piper or Realtime voice support is missing from $APP_DIR/.venv." >&2
    echo "Run: cd $APP_DIR && ./scripts/install.sh" >&2
    exit 1
  fi

  if [ ! -f "$PIPER_MODEL" ] || [ ! -f "$PIPER_CONFIG" ]; then
    echo "Error: default French Piper voice is missing: $PIPER_MODEL" >&2
    echo "Run: cd $APP_DIR && ./scripts/install.sh" >&2
    exit 1
  fi
}

require_node_stack() {
  if ! command -v node >/dev/null 2>&1; then
    echo "Error: node is not installed. Install Node.js >= 20.20.0 for the stdio MCP servers." >&2
    exit 1
  fi
  if ! command -v npm >/dev/null 2>&1; then
    echo "Error: npm is not installed. Install npm with Node.js >= 20.20.0 for the stdio MCP servers." >&2
    exit 1
  fi

  local node_version
  node_version="$(node -p "process.versions.node")"
  node -e '
    const version = process.versions.node.split(".").map(Number);
    const ok = version[0] > 20 || (version[0] === 20 && version[1] >= 20);
    if (!ok) process.exit(1);
  ' || {
    echo "Error: Node.js ${node_version} is too old. Install Node.js >= 20.20.0; Node 22 LTS is recommended." >&2
    exit 1
  }
}

install_env_if_missing() {
  local source="$1"
  local target="$2"
  if [ -f "$target" ]; then
    echo "Preserving existing $target"
    return
  fi
  sudo cp "$source" "$target"
  echo "Installed initial $target"
}

ensure_env_value_if_missing() {
  local target="$1"
  local key="$2"
  local value="$3"
  if sudo grep -q "^${key}=" "$target"; then
    return
  fi
  printf '%s=%s\n' "$key" "$value" | sudo tee -a "$target" >/dev/null
  echo "Added $key to existing $target"
}

require_python_stack
require_node_stack

echo "Installing Live Stage Assistant service files..."

sudo install -d -o "$SERVICE_USER" -g "$SERVICE_USER" "$ENV_DIR"
sudo loginctl enable-linger "$SERVICE_USER"
install_env_if_missing "$SCRIPT_DIR/.env.online" "$ONLINE_ENV"
install_env_if_missing "$SCRIPT_DIR/.env.offline" "$OFFLINE_ENV"

# Existing profiles are intentionally preserved. Add only missing OR3 keys so
# upgrades adopt Piper without overwriting site-specific mixer/audio settings.
ensure_env_value_if_missing "$OFFLINE_ENV" "LOCAL_TTS_PROVIDER" "piper"
ensure_env_value_if_missing "$OFFLINE_ENV" "PIPER_VOICE" "$PIPER_VOICE"
ensure_env_value_if_missing "$OFFLINE_ENV" "PIPER_DATA_DIR" "data/piper"
ensure_env_value_if_missing "$OFFLINE_ENV" "PIPER_MODEL_PATH" ""
ensure_env_value_if_missing "$OFFLINE_ENV" "PIPER_CONFIG_PATH" ""
ensure_env_value_if_missing "$OFFLINE_ENV" "PIPER_LENGTH_SCALE" "1.00"
ensure_env_value_if_missing "$OFFLINE_ENV" "LOCAL_TTS_PYTTSX3_FALLBACK" "true"

sudo cp "$SCRIPT_DIR/livestageassistant.service" /etc/systemd/system/livestageassistant.service
sudo cp "$SCRIPT_DIR/livestageassistant" /usr/local/bin/livestageassistant

sudo chown -R "$SERVICE_USER:$SERVICE_USER" "$ENV_DIR"
sudo chmod 755 "$ENV_DIR"
sudo chmod 644 "$ONLINE_ENV" "$OFFLINE_ENV"
sudo chmod 644 /etc/systemd/system/livestageassistant.service
sudo chmod +x /usr/local/bin/livestageassistant

sudo systemctl daemon-reload

echo
echo "Installation complete. Existing runtime env profiles were preserved and missing OR3 Piper keys were added."
echo "Next steps:"
echo "  1) Check $ONLINE_ENV and $OFFLINE_ENV"
echo "  2) Make sure /home/pi/XMSeries-MCP and /home/pi/QLCPlus-MCP exist and are built"
echo "  3) Run: livestageassistant auto"
echo "  4) Test locally: livestageassistant health"
echo "  5) From another machine: open http://<raspberry-ip>:8765"
