#!/usr/bin/env bash
set -euo pipefail

SUDO=""
if [ "$(id -u)" -ne 0 ]; then
  if ! command -v sudo >/dev/null 2>&1; then
    printf 'sudo is required when this script is not run as root.\n' >&2
    exit 1
  fi
  SUDO="sudo"
fi

if [ "$(uname -s)" != "Linux" ]; then
  printf 'This script installs Linux dependencies only.\n' >&2
  exit 1
fi

if ! command -v apt-get >/dev/null 2>&1 || ! command -v dpkg >/dev/null 2>&1; then
  printf 'This script currently supports apt/dpkg based Linux distributions.\n' >&2
  exit 1
fi

dpkg_arch="$(dpkg --print-architecture)"
machine="$(uname -m)"
packages=()

case "$dpkg_arch:$machine" in
  amd64:*|*:x86_64)
    if ! dpkg --print-foreign-architectures | grep -qx 'i386'; then
      printf 'Enabling i386 multiarch for SteamCMD...\n'
      $SUDO dpkg --add-architecture i386
    fi
    packages=(lib32gcc-s1 libc6:i386 libstdc++6:i386)
    ;;
  i386:*|*:i386|*:i686)
    packages=(libgcc-s1 libc6 libstdc++6)
    ;;
  arm64:*|*:aarch64)
    printf 'SteamCMD for Linux includes an x86 32-bit binary and cannot run natively on arm64/aarch64.\n' >&2
    printf 'Use an amd64/x86_64 Linux environment for native SteamCMD support.\n' >&2
    exit 1
    ;;
  *)
    printf 'Unsupported Linux architecture for SteamCMD: dpkg=%s uname=%s\n' "$dpkg_arch" "$machine" >&2
    exit 1
    ;;
esac

printf 'Updating apt package lists...\n'
$SUDO apt-get update

installable=()
missing=()
for package in "${packages[@]}"; do
  if apt-cache policy "$package" | grep -q 'Candidate: (none)'; then
    missing+=("$package")
  else
    installable+=("$package")
  fi
done

if [ "${#missing[@]}" -ne 0 ]; then
  printf 'The following SteamCMD runtime packages are not available from configured apt sources:\n' >&2
  printf '  %s\n' "${missing[@]}" >&2
  printf 'Enable the required Ubuntu/Debian repositories and rerun this script.\n' >&2
  exit 1
fi

printf 'Installing SteamCMD runtime packages: %s\n' "${installable[*]}"
$SUDO apt-get install -y "${installable[@]}"

if [ -e /lib/ld-linux.so.2 ]; then
  printf 'SteamCMD Linux runtime is ready: /lib/ld-linux.so.2 exists.\n'
else
  printf 'Installed packages, but /lib/ld-linux.so.2 is still missing.\n' >&2
  exit 1
fi
