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
  --install-build-tools BOOL  true | false | auto (default: auto, controls build-tool dependency checks)
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

missing_deps=()

run_privileged() {
  if [[ "${EUID:-$(id -u)}" -eq 0 ]]; then
    "$@"
    return
  fi
  require_cmd sudo
  sudo "$@"
}

record_missing_cmd() {
  local cmd="$1"
  if ! command -v "$cmd" >/dev/null 2>&1; then
    missing_deps+=("$cmd")
  fi
}

detect_pkg_manager() {
  if command -v apt-get >/dev/null 2>&1; then
    printf '%s\n' "apt"
    return
  fi
  if command -v dnf >/dev/null 2>&1; then
    printf '%s\n' "dnf"
    return
  fi
  die "unsupported Linux package manager. Use apt or dnf."
}

install_missing_linux_deps() {
  local pkg_manager="$1"
  local -a packages=()

  for dep in "${missing_deps[@]}"; do
    case "$pkg_manager:$dep" in
      apt:python3)
        packages+=(python3 python3-venv python3-pip)
        ;;
      apt:git)
        packages+=(git)
        ;;
      apt:openocd)
        packages+=(openocd)
        ;;
      apt:make)
        packages+=(make)
        ;;
      apt:cmake)
        packages+=(cmake)
        ;;
      apt:arm-none-eabi-gcc)
        packages+=(gcc-arm-none-eabi)
        ;;
      dnf:python3)
        packages+=(python3 python3-pip)
        ;;
      dnf:git)
        packages+=(git)
        ;;
      dnf:openocd)
        packages+=(openocd)
        ;;
      dnf:make)
        packages+=(make)
        ;;
      dnf:cmake)
        packages+=(cmake)
        ;;
      dnf:arm-none-eabi-gcc)
        packages+=(arm-none-eabi-gcc-cs)
        ;;
    esac
  done

  if [[ "${#packages[@]}" -eq 0 ]]; then
    return
  fi

  echo "Installing missing packages via $pkg_manager: ${packages[*]}"
  if [[ "$pkg_manager" == "apt" ]]; then
    run_privileged apt-get update
    run_privileged apt-get install -y "${packages[@]}"
  else
    run_privileged dnf install -y "${packages[@]}"
  fi
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

ensure_install_target_ready

echo "[1/4] Checking required tools"
record_missing_cmd git
record_missing_cmd python3
if [[ "$FLASH_CAPABLE" -eq 1 || "$CAPTURE_CAPABLE" -eq 1 ]]; then
  record_missing_cmd openocd
fi
if [[ "$INSTALL_BUILD_TOOLS" == "true" ]]; then
  record_missing_cmd make
  record_missing_cmd cmake
  record_missing_cmd arm-none-eabi-gcc
fi

if [[ "${#missing_deps[@]}" -gt 0 ]]; then
  PKG_MANAGER="$(detect_pkg_manager)"
  install_missing_linux_deps "$PKG_MANAGER"
fi

require_cmd git
require_cmd python3
if [[ "$FLASH_CAPABLE" -eq 1 || "$CAPTURE_CAPABLE" -eq 1 ]]; then
  require_cmd openocd
fi
if [[ "$INSTALL_BUILD_TOOLS" == "true" ]]; then
  require_cmd make
  require_cmd cmake
  require_cmd arm-none-eabi-gcc
fi

echo "[2/4] Cloning or updating RTMS repo"
ensure_install_target_ready
mkdir -p "$(dirname "$INSTALL_DIR")"
if [[ -d "$INSTALL_DIR/.git" ]]; then
  git -C "$INSTALL_DIR" fetch --all --tags
  git -C "$INSTALL_DIR" pull --ff-only
else
  git clone "$REPO_URL" "$INSTALL_DIR"
fi

echo "[3/4] Writing agent env file"
ENV_FILE="$INSTALL_DIR/.agent-env.sh"
cat > "$ENV_FILE" <<ENVVARS
export RANGE_TEST_INSTALL_DIR="$INSTALL_DIR"
export RANGE_TEST_SERVER_URL="$SERVER_URL"
export RANGE_TEST_AGENT_DATA_DIR="$INSTALL_DIR/agent_data"
export RANGE_TEST_SERVER_DATA_DIR="$INSTALL_DIR/server_data"
export RANGE_TEST_OPENOCD_TARGET_CFG="$OPENOCD_TARGET_CFG"
export RANGE_TEST_AGENT_BUILD_CAPABLE=$BUILD_CAPABLE
export RANGE_TEST_AGENT_FLASH_CAPABLE=$FLASH_CAPABLE
export RANGE_TEST_AGENT_CAPTURE_CAPABLE=$CAPTURE_CAPABLE
ENVVARS

echo "[4/4] Basic connectivity check"
if command -v curl >/dev/null 2>&1; then
  if curl --max-time 5 --silent --fail "$SERVER_URL/healthz" >/dev/null; then
    echo "healthz: OK"
  else
    echo "healthz: WARNING (could not reach $SERVER_URL/healthz)"
  fi
fi

cat <<'DONE'

Bootstrap complete.

Next commands:
  cd "$INSTALL_DIR"
  python3 -m venv .venv
  source .venv/bin/activate
  pip install --upgrade pip
  pip install -e .
  source .agent-env.sh
  ./.venv/bin/range-test-server run
  ./.venv/bin/range-test-agent run

DONE
