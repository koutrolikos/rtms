#!/usr/bin/env bash
set -euo pipefail

CALLER_PWD="$(pwd -P)"
SERVER_URL=""
MODE="full"
INSTALL_BUILD_TOOLS="auto"
REPO_URL="https://github.com/koutrolikos/rtms.git"
INSTALL_DIR="$HOME/rtms-agent"
OPENOCD_TARGET_CFG="target/stm32g4x.cfg"

usage() {
  cat <<'USAGE'
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
USAGE
}

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "error: missing required command: $1" >&2
    exit 1
  fi
}

die() {
  echo "error: $*" >&2
  exit 1
}

require_value() {
  local option="$1"
  local value="${2-}"
  if [[ -z "$value" || "$value" == --* ]]; then
    die "missing value for $option"
  fi
}

normalize_path() {
  local input="$1"
  if [[ -z "$input" ]]; then
    die "--install-dir cannot be empty"
  fi
  case "$input" in
    "~")
      printf '%s\n' "$HOME"
      ;;
    "~/"*)
      printf '%s/%s\n' "$HOME" "${input#~/}"
      ;;
    /*)
      printf '%s\n' "$input"
      ;;
    *)
      printf '%s/%s\n' "$CALLER_PWD" "$input"
      ;;
  esac
}

normalize_server_url() {
  local input="$1"
  local trimmed="${input%/}"
  if [[ "$trimmed" != http://* && "$trimmed" != https://* ]]; then
    die "--server-url must start with http:// or https://"
  fi
  if [[ "$trimmed" == "http://0.0.0.0"* || "$trimmed" == "https://0.0.0.0"* ]]; then
    die "--server-url cannot use 0.0.0.0; use 127.0.0.1 for same-machine development or a routable host/IP for remote agents"
  fi
  printf '%s\n' "$trimmed"
}

run_privileged() {
  if [[ "${EUID:-$(id -u)}" -eq 0 ]]; then
    "$@"
    return
  fi
  require_cmd sudo
  sudo "$@"
}

ensure_install_target_ready() {
  if [[ -e "$INSTALL_DIR" && ! -d "$INSTALL_DIR" ]]; then
    die "--install-dir points to a file: $INSTALL_DIR"
  fi
  if [[ -d "$INSTALL_DIR" && ! -d "$INSTALL_DIR/.git" ]]; then
    if [[ -n "$(find "$INSTALL_DIR" -mindepth 1 -maxdepth 1 -print -quit 2>/dev/null)" ]]; then
      die "--install-dir already exists and is not an RTMS git checkout: $INSTALL_DIR"
    fi
  fi
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --server-url=*)
      SERVER_URL="${1#*=}"
      shift
      ;;
    --server-url)
      require_value "$1" "${2-}"
      SERVER_URL="$2"
      shift 2
      ;;
    --mode=*)
      MODE="${1#*=}"
      shift
      ;;
    --mode)
      require_value "$1" "${2-}"
      MODE="$2"
      shift 2
      ;;
    --install-build-tools=*)
      INSTALL_BUILD_TOOLS="${1#*=}"
      shift
      ;;
    --install-build-tools)
      require_value "$1" "${2-}"
      INSTALL_BUILD_TOOLS="$2"
      shift 2
      ;;
    --repo-url=*)
      REPO_URL="${1#*=}"
      shift
      ;;
    --repo-url)
      require_value "$1" "${2-}"
      REPO_URL="$2"
      shift 2
      ;;
    --install-dir=*)
      INSTALL_DIR="${1#*=}"
      shift
      ;;
    --install-dir)
      require_value "$1" "${2-}"
      INSTALL_DIR="$2"
      shift 2
      ;;
    --openocd-target-cfg=*)
      OPENOCD_TARGET_CFG="${1#*=}"
      shift
      ;;
    --openocd-target-cfg)
      require_value "$1" "${2-}"
      OPENOCD_TARGET_CFG="$2"
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

SERVER_URL="$(normalize_server_url "$SERVER_URL")"
INSTALL_DIR="$(normalize_path "$INSTALL_DIR")"

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

echo "[1/6] Installing OS packages via $PKG_MANAGER"
if [[ "$PKG_MANAGER" == "apt" ]]; then
  run_privileged apt-get update
  run_privileged apt-get install -y python3 python3-venv python3-pip git curl openocd
  if [[ "$INSTALL_BUILD_TOOLS" == "true" ]]; then
    run_privileged apt-get install -y make cmake gcc-arm-none-eabi
  fi
else
  run_privileged dnf install -y python3 python3-pip git curl openocd
  if [[ "$INSTALL_BUILD_TOOLS" == "true" ]]; then
    run_privileged dnf install -y make cmake arm-none-eabi-gcc-cs
  fi
fi

echo "[2/6] Cloning or updating RTMS repo"
ensure_install_target_ready
mkdir -p "$(dirname "$INSTALL_DIR")"
if [[ -d "$INSTALL_DIR/.git" ]]; then
  git -C "$INSTALL_DIR" fetch --all --tags
  git -C "$INSTALL_DIR" pull --ff-only
else
  git clone "$REPO_URL" "$INSTALL_DIR"
fi

echo "[3/6] Creating Python virtualenv and installing package"
require_cmd python3
python3 -m venv "$INSTALL_DIR/.venv"
"$INSTALL_DIR/.venv/bin/pip" install --upgrade pip
"$INSTALL_DIR/.venv/bin/pip" install -e "$INSTALL_DIR"

echo "[4/6] Writing agent env file"
ENV_FILE="$INSTALL_DIR/.agent-env.sh"
cat > "$ENV_FILE" <<ENVVARS
export PATH="$INSTALL_DIR/.venv/bin:\$PATH"
export RANGE_TEST_INSTALL_DIR="$INSTALL_DIR"
export RANGE_TEST_SERVER_URL="$SERVER_URL"
export RANGE_TEST_AGENT_DATA_DIR="$INSTALL_DIR/agent_data"
export RANGE_TEST_SERVER_DATA_DIR="$INSTALL_DIR/server_data"
export RANGE_TEST_OPENOCD_TARGET_CFG="$OPENOCD_TARGET_CFG"
export RANGE_TEST_AGENT_BUILD_CAPABLE=$BUILD_CAPABLE
export RANGE_TEST_AGENT_FLASH_CAPABLE=$FLASH_CAPABLE
export RANGE_TEST_AGENT_CAPTURE_CAPABLE=$CAPTURE_CAPABLE
ENVVARS

echo "[5/6] Installing command shims"
WRAPPER_DIR="$HOME/.local/bin"
mkdir -p "$WRAPPER_DIR"

cat > "$WRAPPER_DIR/range-test-server" <<SHIM
#!/usr/bin/env bash
set -euo pipefail
if [[ -f "$INSTALL_DIR/.agent-env.sh" ]]; then
  source "$INSTALL_DIR/.agent-env.sh"
fi
if [[ "\${1:-}" == "run" ]]; then
  shift
fi
exec "$INSTALL_DIR/.venv/bin/range-test-server" "\$@"
SHIM

cat > "$WRAPPER_DIR/range-test-agent" <<SHIM
#!/usr/bin/env bash
set -euo pipefail
if [[ -f "$INSTALL_DIR/.agent-env.sh" ]]; then
  source "$INSTALL_DIR/.agent-env.sh"
fi
exec "$INSTALL_DIR/.venv/bin/range-test-agent" "\$@"
SHIM

chmod +x "$WRAPPER_DIR/range-test-server" "$WRAPPER_DIR/range-test-agent"

SHELL_RC="$HOME/.profile"
if [[ "${SHELL:-}" == *"zsh" ]]; then
  SHELL_RC="$HOME/.zshrc"
elif [[ "${SHELL:-}" == *"bash" ]]; then
  SHELL_RC="$HOME/.bashrc"
fi
PATH_LINE='export PATH="$HOME/.local/bin:$PATH"'
if [[ -f "$SHELL_RC" ]]; then
  if ! grep -Fq "$PATH_LINE" "$SHELL_RC"; then
    printf "\n%s\n" "$PATH_LINE" >> "$SHELL_RC"
  fi
else
  printf "%s\n" "$PATH_LINE" > "$SHELL_RC"
fi

echo "[6/6] Basic connectivity check"
if command -v curl >/dev/null 2>&1; then
  if curl --max-time 5 --silent --fail "$SERVER_URL/healthz" >/dev/null; then
    echo "healthz: OK"
  else
    echo "healthz: WARNING (could not reach $SERVER_URL/healthz)"
  fi
fi

cat <<'DONE'

Bootstrap complete.

Open a new terminal, then run:
  range-test-server run
  range-test-agent run

DONE
