#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

find_server_bin() {
  local candidate
  if command -v range-test-server >/dev/null 2>&1; then
    command -v range-test-server
    return 0
  fi
  for candidate in \
    "$REPO_ROOT/.venv/bin/range-test-server" \
    "${RANGE_TEST_INSTALL_DIR:-$HOME/rtms-agent}/.venv/bin/range-test-server"
  do
    if [[ -x "$candidate" ]]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done
  return 1
}

source_server_env() {
  local env_file
  for env_file in \
    "$REPO_ROOT/.agent-env.sh" \
    "${RANGE_TEST_INSTALL_DIR:-$HOME/rtms-agent}/.agent-env.sh"
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
  echo "error: could not find range-test-server on PATH, in $REPO_ROOT/.venv, or in ${RANGE_TEST_INSTALL_DIR:-$HOME/rtms-agent}/.venv" >&2
  exit 1
}

exec "$SERVER_BIN" "$@"
