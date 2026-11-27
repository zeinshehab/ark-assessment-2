#!/usr/bin/env bash
# Deploy one image tag (a git commit SHA published by CI) and roll back automatically if it isn't healthy.
#
#   ./deploy/deploy.sh 3f2a9c1        deploy that build
#   ./deploy/rollback.sh              go back to the previous good build
#
# What "good" means: /readyz answers 200 *and* reports the version we just deployed, within READY_TIMEOUT seconds.
# /readyz checks the configuration loaded, the data volume is writable, and the MCP server starts (no LLM calls).
#
# Exit codes: 0 deployed | 1 failed but rolled back to the previous version | 2 failed and could not recover
#
# Environment (optional):
#   READY_TIMEOUT=90   seconds to wait for readiness
#   TUNNEL=1           also run the Cloudflare Tunnel (set 0 for a local check without a tunnel token)
#   SKIP_PULL=1        use local images instead of pulling (local testing)
#   APP_IMAGE=...      image repository (default: ghcr.io/zeinshehab/ark-assessment-2)
set -euo pipefail
cd "$(dirname "$0")"

TAG="${1:?usage: deploy.sh <image tag, e.g. the short git SHA from the CI summary>}"
READY_TIMEOUT="${READY_TIMEOUT:-90}"
READY_URL="http://127.0.0.1:8000/readyz"
COMPOSE=(docker compose -f compose.yaml)
[ "${TUNNEL:-1}" = 1 ] && COMPOSE+=(--profile tunnel)

CURRENT="$(cat .deployed 2>/dev/null || true)"   # the version serving right now (last good deploy)

log() { echo "[deploy $(date -u +%H:%M:%SZ)] $*"; }

start() {  # start (or replace) the stack on a given tag
  if [ "${SKIP_PULL:-0}" != 1 ]; then APP_TAG="$1" "${COMPOSE[@]}" pull --quiet; fi
  APP_TAG="$1" "${COMPOSE[@]}" up -d --remove-orphans
}

ready() {  # wait until /readyz is 200 and reports this version
  local deadline=$((SECONDS + READY_TIMEOUT)) body
  while [ $SECONDS -lt $deadline ]; do
    if body="$(curl -fsS --max-time 5 "$READY_URL" 2>/dev/null)" && [[ "$body" == *"\"version\":\"$1\""* ]]; then
      log "ready: $body"
      return 0
    fi
    sleep 3
  done
  return 1
}

log "deploying $TAG (currently serving: ${CURRENT:-nothing})"
start "$TAG"
if ready "$TAG"; then
  [ -n "$CURRENT" ] && [ "$CURRENT" != "$TAG" ] && echo "$CURRENT" > .deployed.previous
  echo "$TAG" > .deployed
  log "OK: $TAG is serving"
  exit 0
fi

log "FAILED: $TAG did not become ready within ${READY_TIMEOUT}s. Last log lines from the app:"
APP_TAG="$TAG" "${COMPOSE[@]}" logs --no-log-prefix --tail 20 app 2>&1 | uniq || true   # a crash loop repeats the same line
if [ -z "$CURRENT" ] || [ "$CURRENT" = "$TAG" ]; then
  log "nothing to roll back to; the app is DOWN. Fix the configuration and deploy again."
  exit 2
fi

log "rolling back to $CURRENT"
start "$CURRENT"
if ready "$CURRENT"; then
  log "ROLLED BACK: $CURRENT is serving again. $TAG was not deployed."
  exit 1
fi
log "ROLLBACK FAILED: the app is DOWN. Check 'docker compose -f deploy/compose.yaml logs app'."
exit 2
