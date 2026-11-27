#!/usr/bin/env bash
# Manual rollback: redeploy the build that was serving before the last successful deploy.
# Use it when a release passes its readiness check but turns out to be bad in use (e.g. runs start failing).
set -euo pipefail
cd "$(dirname "$0")"
PREVIOUS="$(cat .deployed.previous 2>/dev/null || true)"
[ -n "$PREVIOUS" ] || { echo "no previous deployment recorded (.deployed.previous)"; exit 2; }
echo "rolling back from $(cat .deployed) to $PREVIOUS"
exec ./deploy.sh "$PREVIOUS"
