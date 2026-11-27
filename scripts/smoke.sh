#!/usr/bin/env bash
# Smoke-test a built image the way production runs it. No LLM calls: dummy keys, and only endpoints that don't
# call a provider.   usage: scripts/smoke.sh <image>
#
#  1. good config: becomes healthy, /readyz passes (MCP server starts), the API refuses requests without a login
#  2. bad config (empty key): exits with code 2, names the setting, and never prints a key
set -euo pipefail
IMAGE="${1:?usage: scripts/smoke.sh <image>}"
WORK="$(mktemp -d)"
MOUNT="$WORK"
command -v cygpath >/dev/null && MOUNT="$(cygpath -w "$WORK")"  # Git Bash on Windows: Docker needs a Windows path
trap 'docker rm -f smoke-good smoke-bad >/dev/null 2>&1 || true; rm -rf "$WORK"' EXIT

GROQ="gsk_smokeTestDummyKey0123456789"
GEMINI="AIzaSmokeTestDummyKey0123456789ab"
mkdir -p "$WORK/secrets"
printf '%s\n' "$GROQ" > "$WORK/secrets/groq_key"
printf '%s\n' "$GEMINI" > "$WORK/secrets/gemini_key"
chmod 644 "$WORK/secrets/"*   # readable by the image's non-root user (uid 10001)

run() {  # the same hardening flags as deploy/compose.yaml
  docker run -d --name "$1" --read-only --tmpfs /tmp --cap-drop ALL --security-opt no-new-privileges \
    -v "$MOUNT/secrets:/run/secrets:ro" -e CF_ACCESS_TEAM_DOMAIN=smoke.cloudflareaccess.com -e CF_ACCESS_AUD=smoke \n    -e OPERATOR_CONTACT=ops@example.com \
    -p "$2:8000" "$IMAGE" >/dev/null
}

echo "== 1. good configuration"
run smoke-good 18000
for _ in $(seq 1 30); do
  [ "$(docker inspect -f '{{.State.Health.Status}}' smoke-good)" = healthy ] && break
  sleep 2
done
docker inspect -f 'health: {{.State.Health.Status}}' smoke-good
curl -fsS http://127.0.0.1:18000/healthz && echo
curl -fsS http://127.0.0.1:18000/readyz && echo
code=$(curl -s -o "$MOUNT/body" -w '%{http_code}' http://127.0.0.1:18000/api/runs)
[ "$code" = 401 ] || { echo "expected 401 without a login, got $code"; exit 1; }
echo "API without login -> 401 (ok)"
if docker logs smoke-good 2>&1 | grep -qE "$GROQ|$GEMINI"; then echo "a key appeared in the logs"; exit 1; fi
echo "no keys in the logs (ok)"

echo "== 2. empty key: must refuse to start"
: > "$WORK/secrets/groq_key"
run smoke-bad 18001
exit_code=$(docker wait smoke-bad)
logs=$(docker logs smoke-bad 2>&1)
echo "$logs"
[ "$exit_code" = 2 ] || { echo "expected exit code 2, got $exit_code"; exit 1; }
echo "$logs" | grep -q GROQ_KEY || { echo "the error does not name GROQ_KEY"; exit 1; }
if echo "$logs" | grep -q "$GEMINI"; then echo "the other key leaked into the error"; exit 1; fi
echo "smoke test passed"
