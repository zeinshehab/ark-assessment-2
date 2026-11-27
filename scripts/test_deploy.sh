#!/usr/bin/env bash
# Exercise deploy/deploy.sh end to end, the way production runs it (tunnel profile on), with two images made from
# the one CI just built:  a good release, then a broken one that must be rolled back automatically.
#
#   scripts/test_deploy.sh <image>        e.g. scripts/test_deploy.sh crosscheck:ci
#
# Runs in a temporary copy of deploy/ with dummy config and secrets, so it never touches a real .env.prod or
# secrets/. Uses port 8000 and its own Compose project name: stop a local stack first.
set -euo pipefail
IMAGE="${1:?usage: scripts/test_deploy.sh <image>}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WORK="$(mktemp -d)"
export COMPOSE_PROJECT_NAME=crosscheck-deploy-test APP_IMAGE=crosscheck-test SKIP_PULL=1 TUNNEL=1 READY_TIMEOUT=60
cleanup() {
  (cd "$WORK" && APP_TAG=good docker compose -f compose.yaml --profile tunnel down -v >/dev/null 2>&1) || true
  rm -rf "$WORK"
}
trap cleanup EXIT

# Two releases: "good" is the image as built; "bad" needs configuration this host doesn't have (an invalid value
# set in the image, which .env.prod doesn't override), so it crash-loops at startup like a config-drift release.
printf 'FROM %s\nENV APP_VERSION=good\n' "$IMAGE" | docker build -q -t crosscheck-test:good - >/dev/null
printf 'FROM %s\nENV APP_VERSION=bad GROQ_DAILY_TOKEN_BUDGET=-5\n' "$IMAGE" | docker build -q -t crosscheck-test:bad - >/dev/null

cp "$ROOT/deploy/compose.yaml" "$ROOT/deploy/deploy.sh" "$ROOT/deploy/rollback.sh" "$ROOT/deploy/.env.prod.example" "$WORK/"
cp "$WORK/.env.prod.example" "$WORK/.env.prod"
mkdir "$WORK/secrets"
printf 'gsk_deployTestDummyKey0123456789\n' > "$WORK/secrets/groq_key"
printf 'AIzaDeployTestDummyKey0123456789ab\n' > "$WORK/secrets/gemini_key"
printf 'dummy-tunnel-token\n' > "$WORK/secrets/tunnel_token"   # cloudflared won't connect; its startup order still matters
chmod 644 "$WORK/secrets/"*

expect() {  # expect <exit code> <tag>: run deploy.sh <tag> and check the result
  local code=0
  bash "$WORK/deploy.sh" "$2" || code=$?
  [ "$code" = "$1" ] || { echo "deploy.sh $2 exited $code, expected $1"; exit 1; }
}
serving() {  # serving <tag>: the recorded version and the live /readyz must both say <tag>
  [ "$(cat "$WORK/.deployed")" = "$1" ] || { echo "expected .deployed=$1"; exit 1; }
  curl -fsS http://127.0.0.1:8000/readyz | grep -q "\"version\":\"$1\"" || { echo "expected /readyz to report $1"; exit 1; }
  echo "serving $1 (ok)"
}

echo "== deploy a good release";                 expect 0 good; serving good
echo "== deploy a broken release: must roll back"; expect 1 bad;  serving good
echo "deploy test passed"
