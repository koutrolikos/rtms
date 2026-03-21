#!/usr/bin/env bash
set -euo pipefail

SERVER_URL=""
MODE="full"
INSTALL_BUILD_TOOLS="auto"
REPO_URL="https://github.com/koutrolikos/rtms.git"
INSTALL_DIR="$HOME/rtms-agent"
OPENOCD_TARGET_CFG="target/stm32g4x.cfg"

usage() {
  cat <<'EOF'
Bootstrap RTMS agent on Linux.

Usage:
  bootstrap_agent_linux.sh --server-url URL [options]

Required:
  --server-url URL            RTMS server URL, example: http://172.20.10.3:8000

Options:
  --mode MODE                 full | build-only | flash-capture (default: full)
  --install-build-tools BOOL  true | false | auto (default: auto)
  --repo-url URL              RTMS git URL (default: https://github.com/koutrolikos/rtms.git)
  --install-dir PATH          Install path (default: ~/rtms-agent)
  --openocd-target-cfg CFG    OpenOCD target cfg (default: target/stm32g4x.cfg)
  -h, --help                  Show help

Examples:
  ./scripts/bootstrap_agent_linux.sh --server-url http://172.20.10.3:8000
  ./scripts/bootstrap_agent_linux.sh --server-url http://172.20.10.3:8000 --mode build-only
EOF
}

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "error: missing required command: $1" >&2
    exit 1
  fi
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --server-url)
      SERVER_URL="${2:-}"
      shift 2
      ;;
    --mode)
      MODE="${2:-}"
      shift 2
      ;;
    --install-build-tools)
      INSTALL_BUILD_TOOLS="${2:-}"
      shift 2
      ;;
    --repo-url)
      REPO_URL="${2:-}"
      shift 2
      ;;
    --install-dir)
      INSTALL_DIR="${2:-}"
      shift 2
      ;;
    --openocd-target-cfg)
      OPENOCD_TARGET_CFG="${2:-}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "error: unknown argument: $1" >&2
      usage
      exit 1
      ;;
  esac
done

if [[ -z "$SERVER_URL" ]]; then
  echo "error: --server-url is required" >&2
  usage
  exit 1
fi

if [[ "$MODE" != "full" && "$MODE" != "build-only" && "$MODE" != "flash-capture" ]]; then
  echo "error: --mode must be one of: full, build-only, flash-capture" >&2
  exit 1
fi

if [[ "$INSTALL_BUILD_TOOLS" != "true" && "$INSTALL_BUILD_TOOLS" != "false" && "$INSTALL_BUILD_TOOLS" != "auto" ]]; then
  echo "error: --install-build-tools must be true, false, or auto" >&2
  exit 1
fi

if [[ "$INSTALL_BUILD_TOOLS" == "auto" ]]; then
  if [[ "$MODE" == "build-only" || "$MODE" == "full" ]]; then
    INSTALL_BUILD_TOOLS="true"
  else
    INSTALL_BUILD_TOOLS="false"
  fi
fi

if [[ "$MODE" == "full" ]]; then
  BUILD_CAPABLE=1
  FLASH_CAPABLE=1
  CAPTURE_CAPABLE=1
elif [[ "$MODE" == "build-only" ]]; then
  BUILD_CAPABLE=1
  FLASH_CAPABLE=0
  CAPTURE_CAPABLE=0
else
  BUILD_CAPABLE=0
  FLASH_CAPABLE=1
  CAPTURE_CAPABLE=1
fi

if command -v apt-get >/dev/null 2>&1; then
  PKG_MANAGER="apt"
elif command -v dnf >/dev/null 2>&1; then
  PKG_MANAGER="dnf"
else
  echo "error: unsupported Linux package manager. Use apt or dnf." >&2
  exit 1
fi

echo "[1/5] Installing OS packages via $PKG_MANAGER"
if [[ "$PKG_MANAGER" == "apt" ]]; then
  sudo apt-get update
  sudo apt-get install -y python3 python3-venv python3-pip git curl openocd
  if [[ "$INSTALL_BUILD_TOOLS" == "true" ]]; then
    sudo apt-get install -y make cmake gcc-arm-none-eabi
  fi
else
  sudo dnf install -y python3 python3-pip git curl openocd
  if [[ "$INSTALL_BUILD_TOOLS" == "true" ]]; then
    sudo dnf install -y make cmake arm-none-eabi-gcc-cs
  fi
fi

echo "[2/5] Cloning or updating RTMS repo"
mkdir -p "$(dirname "$INSTALL_DIR")"
if [[ -d "$INSTALL_DIR/.git" ]]; then
  git -C "$INSTALL_DIR" fetch --all --tags
  git -C "$INSTALL_DIR" pull --ff-only
else
  git clone "$REPO_URL" "$INSTALL_DIR"
fi

echo "[3/5] Creating Python virtualenv and installing package"
require_cmd python3
python3 -m venv "$INSTALL_DIR/.venv"
"$INSTALL_DIR/.venv/bin/pip" install --upgrade pip
"$INSTALL_DIR/.venv/bin/pip" install -e "$INSTALL_DIR"

echo "[4/5] Writing agent env file"
ENV_FILE="$INSTALL_DIR/.agent-env.sh"
cat > "$ENV_FILE" <<EOF
export RANGE_TEST_SERVER_URL="$SERVER_URL"
export RANGE_TEST_OPENOCD_TARGET_CFG="$OPENOCD_TARGET_CFG"
export RANGE_TEST_AGENT_BUILD_CAPABLE=$BUILD_CAPABLE
export RANGE_TEST_AGENT_FLASH_CAPABLE=$FLASH_CAPABLE
export RANGE_TEST_AGENT_CAPTURE_CAPABLE=$CAPTURE_CAPABLE
EOF

echo "[5/5] Basic connectivity check"
if command -v curl >/dev/null 2>&1; then
  if curl --max-time 5 --silent --fail "$SERVER_URL/healthz" >/dev/null; then
    echo "healthz: OK"
  else
    echo "healthz: WARNING (could not reach $SERVER_URL/healthz)"
  fi
fi

cat <<EOF

Bootstrap complete.

Next commands:
  cd "$INSTALL_DIR"
  source .venv/bin/activate
  source .agent-env.sh
  range-test-agent run

EOF
