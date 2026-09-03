#!/bin/zsh
set -euo pipefail
cd "${0:A:h}"
if [[ -f .env ]]; then
  set -a
  source .env
  set +a
fi
: "${FOUNDRY_ENDPOINT:?Add FOUNDRY_ENDPOINT to .env}"
: "${FOUNDRY_API_KEY:?Add FOUNDRY_API_KEY to .env}"
: "${FOUNDRY_DEPLOYMENT:?Add FOUNDRY_DEPLOYMENT to .env}"
export PROXY_HOST="${PROXY_HOST:-127.0.0.1}"
export PROXY_PORT="${PROXY_PORT:-8787}"
export ANTHROPIC_BASE_URL="http://${PROXY_HOST}:${PROXY_PORT}"
# Local-only placeholder: the proxy does not forward this to Foundry.
export ANTHROPIC_AUTH_TOKEN="${LOCAL_GATEWAY_TOKEN:-local-proxy}"
export CLAUDE_CODE_ATTRIBUTION_HEADER=0
python3 proxy.py &
proxy_pid=$!
trap 'kill "$proxy_pid" 2>/dev/null || true' EXIT INT TERM
for _ in {1..30}; do
  curl -fsS "$ANTHROPIC_BASE_URL/health" >/dev/null 2>&1 && break
  sleep 0.1
done
curl -fsS "$ANTHROPIC_BASE_URL/health" >/dev/null
claude "$@"
