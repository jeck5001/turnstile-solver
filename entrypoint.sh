#!/usr/bin/env bash
set -euo pipefail
cd /app

HOST="${TURNSTILE_HOST:-0.0.0.0}"
PORT="${TURNSTILE_PORT:-5072}"
THREAD="${TURNSTILE_THREAD:-2}"
BROWSER_TYPE="${TURNSTILE_BROWSER_TYPE:-camoufox}"
DEBUG_FLAG=()
if [[ "${TURNSTILE_DEBUG:-0}" == "1" || "${TURNSTILE_DEBUG:-true}" == "true" ]]; then
  DEBUG_FLAG=(--debug)
fi

PROXY_FLAG=()
if [[ "${TURNSTILE_PROXY:-0}" == "1" || "${TURNSTILE_PROXY:-false}" == "true" ]]; then
  PROXY_FLAG=(--proxy)
fi

mkdir -p /app/logs /app/keys

echo "[turnstile-solver] browser=${BROWSER_TYPE} thread=${THREAD} ${HOST}:${PORT} lazy=${TURNSTILE_LAZY:-1}"
exec python api_solver.py \
  --browser_type "${BROWSER_TYPE}" \
  --thread "${THREAD}" \
  --host "${HOST}" \
  --port "${PORT}" \
  "${DEBUG_FLAG[@]}" \
  "${PROXY_FLAG[@]}"
