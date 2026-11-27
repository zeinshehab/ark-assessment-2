#!/usr/bin/env bash
# Deploy one image tag (a git commit SHA published by CI) and roll back automatically if it isn't healthy.
#
#   ./deploy/deploy.sh 3f2a9c1        deploy that build
#   ./deploy/rollback.sh              go back to the previous good build
#
# Steps:
#   1. pull the new image (if that fails, nothing has changed: stop)
#   2. replace the app container with the new version
#   3. wait until /readyz answers 200 *and* reports the new version (config loaded, data volume writable, MCP server
#      starts; no LLM calls). Give up early if the container is crash-looping.
#   4. ready: start/refresh the rest of the stack (the Cloudflare Tunnel) and record the version
#      not ready: print the app's last log lines and put the previous version back the same way
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

start_app() {  # replace only the app container; --no-deps: don't wait on anything else
  APP_TAG="$1" "${COMPOSE[@]}" up -d --no-deps app
}

ready() {  # wait until /readyz is 200 and reports this version; fail early on a crash loop
  local deadline=$((SECONDS + READY_TIMEOUT)) body container restarts
  container="$(APP_TAG="$1" "${COMPOSE[@]}" ps -q app)"
  while [ $SECONDS -lt $deadline ]; do
    if body="$(curl -fsS --max-time 5 "$READY_URL" 2>/dev/null)" && [[ "$body" == *"\"version\":\"$1\""* ]]; then
      log "ready: $body"
      return 0
    fi
    restarts="$(docker inspect -f '{{.RestartCount}}' "$container" 2>/dev/null || echo 0)"
    if [ "${restarts:-0}" -ge 2 ]; then
      log "the app container has restarted $restarts times: it is crash-looping"
      return 1
    fi
    sleep 3
  done
  log "no ready answer from $1 within ${READY_TIMEOUT}s"
  return 1
}

finish() {  # the app is ready: bring the rest of the stack (tunnel) up to date
  APP_TAG="$1" "${COMPOSE[@]}" up -d --remove-orphans
}

log "deploying $TAG (currently serving: ${CURRENT:-nothing})"
if [ "${SKIP_PULL:-0}" != 1 ]; then  # a missing image changes nothing: stop here, the current version keeps serving
  APP_TAG="$TAG" "${COMPOSE[@]}" pull --quiet || { log "could not pull $TAG; nothing changed"; exit 2; }
fi

start_app "$TAG"
if ready "$TAG"; then
  finish "$TAG"
  [ -n "$CURRENT" ] && [ "$CURRENT" != "$TAG" ] && echo "$CURRENT" > .deployed.previous
  echo "$TAG" > .deployed
  log "OK: $TAG is serving"
  exit 0
fi

log "FAILED: $TAG is not ready. Last log lines from the app:"
APP_TAG="$TAG" "${COMPOSE[@]}" logs --no-log-prefix --tail 20 app 2>&1 | uniq || true   # a crash loop repeats lines
if [ -z "$CURRENT" ] || [ "$CURRENT" = "$TAG" ]; then
  log "nothing to roll back to; the app is DOWN. Fix the configuration and deploy again."
  exit 2
fi

log "rolling back to $CURRENT"
start_app "$CURRENT"
if ready "$CURRENT"; then
  finish "$CURRENT"
  log "ROLLED BACK: $CURRENT is serving again. $TAG was not deployed."
  exit 1
fi
log "ROLLBACK FAILED: the app is DOWN. Check 'docker compose -f deploy/compose.yaml logs app'."
exit 2
