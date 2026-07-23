#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "$SCRIPT_DIR/../.." && pwd)"
DATA_DIR="${PALSITTER_DATA_DIR:-$PROJECT_ROOT/data}"
VENV_DIR="${PALSITTER_VENV_DIR:-$PROJECT_ROOT/.venv}"
PYTHON_BIN="${PALSITTER_PYTHON_BIN:-python3}"
PYTHON_MANAGER="${PALSITTER_PYTHON_MANAGER:-${PALSITTER_ENV_MANAGER:-venv}}"

usage() {
  cat <<'EOF'
Usage: script/linux/palsitter.sh <command> [gui.py arguments]

Commands:
  install    Install Linux packages, Python dependencies, and SteamCMD support.
  run        Run the Palsitter web UI in the foreground.

Environment:
  PALSITTER_PYTHON_MANAGER=venv|asdf|pipenv|uv (default: venv)
EOF
}

die() {
  printf 'Error: %s\n' "$1" >&2
  exit 1
}

validate_python_manager() {
  case "$PYTHON_MANAGER" in
    venv|asdf|pipenv|uv)
      ;;
    *)
      die "unsupported Python manager '$PYTHON_MANAGER'; use venv, asdf, pipenv, or uv"
      ;;
  esac
}

as_root() {
  if [ "$(id -u)" -eq 0 ]; then
    "$@"
    return
  fi
  command -v sudo >/dev/null 2>&1 || die "sudo is required to install Linux packages"
  sudo "$@"
}

install_system_packages() {
  command -v apt-get >/dev/null 2>&1 || die "this installer currently requires apt-get"
  command -v dpkg >/dev/null 2>&1 || die "this installer currently requires dpkg"

  printf 'Installing Python and Git packages...\n'
  as_root apt-get update
  as_root apt-get install -y ca-certificates git python3 python3-pip python3-venv
}

install_python_dependencies() {
  cd "$PROJECT_ROOT"

  case "$PYTHON_MANAGER" in
    venv)
      command -v "$PYTHON_BIN" >/dev/null 2>&1 || die "Python executable not found: $PYTHON_BIN"
      if [ ! -x "$VENV_DIR/bin/python" ]; then
        printf 'Creating Python virtual environment: %s\n' "$VENV_DIR"
        "$PYTHON_BIN" -m venv "$VENV_DIR"
      fi
      printf 'Installing Palsitter Python dependencies with venv...\n'
      "$VENV_DIR/bin/python" -m pip install \
        --disable-pip-version-check \
        --requirement "$PROJECT_ROOT/requirements-runtime.txt"
      ;;
    asdf)
      command -v asdf >/dev/null 2>&1 || die "asdf is not installed or not on PATH"
      PYTHON_BIN="$(asdf which python3 2>/dev/null || asdf which python 2>/dev/null)" \
        || die "asdf has no selected Python version; configure one with 'asdf local python <version>'"
      if [ ! -x "$VENV_DIR/bin/python" ]; then
        printf 'Creating an asdf-backed Python virtual environment: %s\n' "$VENV_DIR"
        "$PYTHON_BIN" -m venv "$VENV_DIR"
      fi
      printf 'Installing Palsitter Python dependencies with asdf-selected Python...\n'
      "$VENV_DIR/bin/python" -m pip install \
        --disable-pip-version-check \
        --requirement "$PROJECT_ROOT/requirements-runtime.txt"
      ;;
    pipenv)
      command -v pipenv >/dev/null 2>&1 || die "pipenv is not installed or not on PATH"
      command -v "$PYTHON_BIN" >/dev/null 2>&1 || die "Python executable not found: $PYTHON_BIN"
      printf 'Creating or selecting the Pipenv environment...\n'
      pipenv --python "$PYTHON_BIN"
      printf 'Installing Palsitter Python dependencies with Pipenv...\n'
      pipenv run python -m pip install \
        --disable-pip-version-check \
        --requirement "$PROJECT_ROOT/requirements-runtime.txt"
      ;;
    uv)
      command -v uv >/dev/null 2>&1 || die "uv is not installed or not on PATH"
      command -v "$PYTHON_BIN" >/dev/null 2>&1 || die "Python executable not found: $PYTHON_BIN"
      printf 'Creating or selecting the uv environment: %s\n' "$VENV_DIR"
      uv venv "$VENV_DIR" --python "$PYTHON_BIN"
      printf 'Installing Palsitter Python dependencies with uv...\n'
      uv pip install \
        --python "$VENV_DIR/bin/python" \
        --requirement "$PROJECT_ROOT/requirements-runtime.txt"
      ;;
  esac
}

prepare_data_directories() {
  printf 'Preparing data directories: %s\n' "$DATA_DIR"
  mkdir -p "$DATA_DIR/config" "$DATA_DIR/profile" "$DATA_DIR/logs"
}

install() {
  [ "$(uname -s)" = "Linux" ] || die "this installer supports Linux only"
  validate_python_manager
  install_system_packages
  install_python_dependencies
  "$SCRIPT_DIR/install-dependencies.sh"
  prepare_data_directories
  printf '\nInstallation complete. Start Palsitter with:\n'
  printf '  %q run\n' "$0"
}

run() {
  [ "$(uname -s)" = "Linux" ] || die "this runner supports Linux only"
  validate_python_manager
  if [ "$PYTHON_MANAGER" = "pipenv" ]; then
    command -v pipenv >/dev/null 2>&1 || die "pipenv is not installed or not on PATH"
    cd "$PROJECT_ROOT"
    pipenv run python -c 'pass' >/dev/null 2>&1 \
      || die "Pipenv environment not found; run '$0 install' first"
  else
    [ -x "$VENV_DIR/bin/python" ] \
      || die "virtual environment not found; run '$0 install' first"
  fi

  prepare_data_directories
  export PALSITTER_CONFIG_DIR="$DATA_DIR/config"
  export PALSITTER_PROFILE_DIR="$DATA_DIR/profile"
  export PALSITTER_LOG_DIR="$DATA_DIR/logs"
  export PALSITTER_HOST="${PALSITTER_HOST:-127.0.0.1}"
  export PALSITTER_PORT="${PALSITTER_PORT:-22368}"

  cd "$PROJECT_ROOT"
  if [ "$PYTHON_MANAGER" = "pipenv" ]; then
    exec pipenv run python "$PROJECT_ROOT/gui.py" "$@"
  fi
  exec "$VENV_DIR/bin/python" "$PROJECT_ROOT/gui.py" "$@"
}

command_name="${1:-}"
case "$command_name" in
  install)
    shift
    [ "$#" -eq 0 ] || die "install does not accept arguments"
    install
    ;;
  run)
    shift
    run "$@"
    ;;
  -h|--help|help)
    usage
    ;;
  *)
    usage >&2
    exit 2
    ;;
esac
