#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "$SCRIPT_DIR/../.." && pwd)"
IMAGE_NAME="palsitter:local"

cd "$PROJECT_ROOT"
mkdir -p docker-volumns/config docker-volumns/profile docker-volumns/logs

if docker image inspect "$IMAGE_NAME" >/dev/null 2>&1; then
  docker compose up -d
else
  docker compose build
  docker compose up -d
fi
