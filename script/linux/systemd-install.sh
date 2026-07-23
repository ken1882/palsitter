#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "$SCRIPT_DIR/../.." && pwd)"
RUNNER="$SCRIPT_DIR/palsitter.sh"
SERVICE_NAME="${PALSITTER_SYSTEMD_SERVICE:-palsitter}"
SERVICE_USER="${PALSITTER_SYSTEMD_USER:-${SUDO_USER:-$(id -un)}}"
SERVICE_GROUP="${PALSITTER_SYSTEMD_GROUP:-}"
DATA_DIR="${PALSITTER_DATA_DIR:-$PROJECT_ROOT/data}"
VENV_DIR="${PALSITTER_VENV_DIR:-$PROJECT_ROOT/.venv}"
PYTHON_MANAGER="${PALSITTER_PYTHON_MANAGER:-${PALSITTER_ENV_MANAGER:-venv}}"
HOST="${PALSITTER_HOST:-127.0.0.1}"
PORT="${PALSITTER_PORT:-22368}"
UNIT_NAME="$SERVICE_NAME.service"
UNIT_PATH="/etc/systemd/system/$UNIT_NAME"
SERVICE_HOME=""

usage() {
  cat <<'EOF'
Usage: script/linux/systemd-install.sh

Install and start a systemd service for the current Palsitter checkout.

Environment:
  PALSITTER_SYSTEMD_SERVICE   Service name (default: palsitter)
  PALSITTER_SYSTEMD_USER      Service user (default: invoking user)
  PALSITTER_SYSTEMD_GROUP     Service group (default: user's primary group)
  PALSITTER_DATA_DIR          Runtime data directory
  PALSITTER_VENV_DIR          Python virtual environment directory
  PALSITTER_PYTHON_MANAGER    venv, asdf, pipenv, or uv
  PALSITTER_HOST              Web UI bind address (default: 127.0.0.1)
  PALSITTER_PORT              Web UI port (default: 22368)
EOF
}

die() {
  printf 'Error: %s\n' "$1" >&2
  exit 1
}

as_root() {
  if [ "$(id -u)" -eq 0 ]; then
    "$@"
    return
  fi
  command -v sudo >/dev/null 2>&1 || die "sudo is required to install a systemd service"
  sudo "$@"
}

as_service_user() {
  if [ "$(id -un)" = "$SERVICE_USER" ]; then
    "$@"
    return
  fi
  if command -v runuser >/dev/null 2>&1; then
    as_root runuser -u "$SERVICE_USER" -- "$@"
    return
  fi
  command -v sudo >/dev/null 2>&1 || die "runuser or sudo is required for service-user checks"
  as_root sudo -u "$SERVICE_USER" -- "$@"
}

unit_quote() {
  local value="$1"
  value="${value//\\/\\\\}"
  value="${value//\"/\\\"}"
  value="${value//%/%%}"
  printf '"%s"' "$value"
}

validate() {
  command -v systemctl >/dev/null 2>&1 || die "systemctl is not installed"
  [ -x "$RUNNER" ] || die "runner is not executable: $RUNNER"
  [ -f "$PROJECT_ROOT/gui.py" ] || die "gui.py was not found in $PROJECT_ROOT"
  id "$SERVICE_USER" >/dev/null 2>&1 || die "service user does not exist: $SERVICE_USER"

  case "$SERVICE_NAME" in
    ''|*[!a-zA-Z0-9_.@-]*) die "invalid systemd service name: $SERVICE_NAME" ;;
  esac
  case "$SERVICE_USER" in
    ''|*[!a-zA-Z0-9_.-]*) die "invalid systemd service user: $SERVICE_USER" ;;
  esac
  case "$PYTHON_MANAGER" in
    venv|asdf|pipenv|uv) ;;
    *) die "unsupported Python manager '$PYTHON_MANAGER'" ;;
  esac
  case "$PORT" in
    ''|*[!0-9]*) die "invalid Palsitter port: $PORT" ;;
  esac

  if [ -z "$SERVICE_GROUP" ]; then
    SERVICE_GROUP="$(id -gn "$SERVICE_USER")"
  fi
  getent group "$SERVICE_GROUP" >/dev/null 2>&1 || die "service group does not exist: $SERVICE_GROUP"
  SERVICE_HOME="$(getent passwd "$SERVICE_USER" | cut -d: -f6)"
  [ -n "$SERVICE_HOME" ] || die "could not resolve the service user's home directory"

  as_service_user test -r "$PROJECT_ROOT/gui.py" \
    || die "service user cannot read the project: $PROJECT_ROOT"
  as_service_user test -x "$RUNNER" \
    || die "service user cannot execute the runner: $RUNNER"
  if [ "$PYTHON_MANAGER" = "pipenv" ]; then
    as_service_user sh -c 'command -v pipenv >/dev/null 2>&1' \
      || die "pipenv is not available to service user $SERVICE_USER"
  else
    as_service_user test -x "$VENV_DIR/bin/python" \
      || die "Python environment not found or inaccessible: $VENV_DIR; run palsitter.sh install first"
  fi
}

write_unit() {
  local temporary
  temporary="$(mktemp)"
  trap 'rm -f "$temporary"' EXIT

  {
    printf '[Unit]\n'
    printf 'Description=Palsitter web UI and managed game servers\n'
    printf 'Wants=network-online.target\n'
    printf 'After=network-online.target\n\n'
    printf '[Service]\n'
    printf 'Type=simple\n'
    printf 'User=%s\n' "$SERVICE_USER"
    printf 'Group=%s\n' "$SERVICE_GROUP"
    printf 'WorkingDirectory=%s\n' "$(unit_quote "$PROJECT_ROOT")"
    printf 'Environment=%s\n' "$(unit_quote "PALSITTER_DATA_DIR=$DATA_DIR")"
    printf 'Environment=%s\n' "$(unit_quote "PALSITTER_VENV_DIR=$VENV_DIR")"
    printf 'Environment=%s\n' "$(unit_quote "PALSITTER_PYTHON_MANAGER=$PYTHON_MANAGER")"
    printf 'Environment=%s\n' "$(unit_quote "PALSITTER_HOST=$HOST")"
    printf 'Environment=%s\n' "$(unit_quote "PALSITTER_PORT=$PORT")"
    printf 'Environment=%s\n' \
      "$(unit_quote "PATH=$SERVICE_HOME/.local/bin:$SERVICE_HOME/.asdf/shims:/usr/local/bin:/usr/bin:/bin")"
    printf 'ExecStart=%s run\n' "$(unit_quote "$RUNNER")"
    printf 'Restart=on-failure\n'
    printf 'RestartSec=5\n'
    printf 'KillMode=control-group\n'
    printf 'KillSignal=SIGINT\n'
    printf 'TimeoutStopSec=90\n'
    printf 'UMask=0077\n\n'
    printf '[Install]\n'
    printf 'WantedBy=multi-user.target\n'
  } > "$temporary"

  as_root install -o root -g root -m 0644 "$temporary" "$UNIT_PATH"
  rm -f "$temporary"
  trap - EXIT
}

main() {
  [ "$(uname -s)" = "Linux" ] || die "this installer supports Linux only"
  [ "${1:-}" = "" ] || {
    case "$1" in
      -h|--help|help) usage; exit 0 ;;
      *) die "this installer does not accept arguments" ;;
    esac
  }

  validate
  write_unit
  as_root systemctl daemon-reload
  as_root systemctl enable "$UNIT_NAME"
  as_root systemctl restart "$UNIT_NAME"

  printf 'Installed and started %s\n' "$UNIT_NAME"
  printf 'View logs with: journalctl -u %s -f\n' "$UNIT_NAME"
  printf 'Stop with: systemctl stop %s\n' "$UNIT_NAME"
}

main "$@"
