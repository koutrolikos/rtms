#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

find_server_bin() {
  local candidate
  for candidate in \
    "${RTMS_INSTALL_DIR:-$HOME/rtms-host}/.venv/bin/rtms-server" \
    "$REPO_ROOT/.venv/bin/rtms-server"
  do
    if [[ -x "$candidate" ]]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done
  if command -v rtms-server >/dev/null 2>&1; then
    command -v rtms-server
    return 0
  fi
  return 1
}

source_server_env() {
  local env_file
  for env_file in \
    "$REPO_ROOT/.rtms-env.sh" \
    "${RTMS_INSTALL_DIR:-$HOME/rtms-host}/.rtms-env.sh"
  do
    if [[ -f "$env_file" ]]; then
      # shellcheck disable=SC1090
      source "$env_file"
      return 0
    fi
  done
  return 1
}

source_server_env || true

SERVER_BIN="$(find_server_bin)" || {
  echo "error: could not find rtms-server on PATH, in $REPO_ROOT/.venv, or in ${RTMS_INSTALL_DIR:-$HOME/rtms-host}/.venv" >&2
  exit 1
}

exec "$SERVER_BIN" "$@"
