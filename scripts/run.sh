#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_dir"

usage() {
  printf '%s\n' \
    'Usage: bash scripts/run.sh [start|stop|logs|status|seed]' \
    '  start   Build and start the local stack; wait for health (default)' \
    '  stop    Stop the stack; keep all named volumes' \
    '  logs    Follow API logs; Ctrl+C stops following only' \
    '  status  Show service and health status' \
    '  seed    Generate two batches of local demo readings'
}

if [[ $# -gt 1 ]]; then usage >&2; exit 2; fi
command="${1:-start}"
case "$command" in
  -h|--help|help) usage; exit 0 ;;
  start|stop|logs|status|seed) ;;
  *) usage >&2; exit 2 ;;
esac

if ! command -v docker >/dev/null 2>&1; then
  printf '%s\n' 'Docker is missing. Enable Docker Desktop integration for this WSL distribution.' >&2
  exit 1
fi
docker compose version >/dev/null
docker info >/dev/null

case "$command" in
  start)
    docker compose up -d --build --wait --wait-timeout 180
    printf '%s\n' "API ready: http://127.0.0.1:${API_PORT:-8001}" \
      "Grafana viewer: http://127.0.0.1:${GRAFANA_PORT:-3000}" \
      'View logs: bash scripts/run.sh logs'
    ;;
  stop) docker compose stop ;;
  logs) docker compose logs --follow --tail 100 api ;;
  status) docker compose ps --all ;;
  seed) docker compose exec -T api python -m canopy.simulator --cycles 2 --interval 1 ;;
esac
