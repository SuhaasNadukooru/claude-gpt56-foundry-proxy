#!/bin/zsh
# Runs local bridge smoke tests. It never prints credentials.
set -euo pipefail
cd "${0:A:h}"
set -a; source .env; set +a
: "${FOUNDRY_ENDPOINT:?Add FOUNDRY_ENDPOINT to .env}"
: "${FOUNDRY_API_KEY:?Add FOUNDRY_API_KEY to .env}"
: "${FOUNDRY_DEPLOYMENT:?Add FOUNDRY_DEPLOYMENT to .env}"
export PROXY_HOST="${PROXY_HOST:-127.0.0.1}"
export PROXY_PORT="${PROXY_PORT:-8787}"
base="http://${PROXY_HOST}:${PROXY_PORT}"
python3 proxy.py >/private/tmp/claude-gpt56-proxy-test.log 2>&1 &
pid=$!
trap 'kill "$pid" 2>/dev/null || true' EXIT INT TERM
for _ in {1..30}; do curl -fsS "$base/health" >/dev/null 2>&1 && break; sleep 0.1; done
curl -fsS "$base/health" | python3 -m json.tool

simple='{"model":"claude-sonnet-4-6","max_tokens":32,"messages":[{"role":"user","content":"Reply with exactly: bridge-ok"}]}'
curl -fsS "$base/v1/messages" -H 'content-type: application/json' -H 'anthropic-version: 2023-06-01' -d "$simple" | python3 -m json.tool

stream='{"model":"claude-sonnet-4-6","max_tokens":32,"stream":true,"messages":[{"role":"user","content":"Reply with exactly: stream-ok"}]}'
curl -fsSN "$base/v1/messages" -H 'content-type: application/json' -H 'anthropic-version: 2023-06-01' -d "$stream" | tail -n 20

tool='{"model":"claude-sonnet-4-6","max_tokens":64,"tools":[{"name":"get_weather","description":"Get current weather for a city","input_schema":{"type":"object","properties":{"city":{"type":"string"}},"required":["city"]}}],"tool_choice":{"type":"tool","name":"get_weather"},"messages":[{"role":"user","content":"Find the weather in Singapore."}]}'
curl -fsS "$base/v1/messages" -H 'content-type: application/json' -H 'anthropic-version: 2023-06-01' -d "$tool" | python3 -m json.tool

print 'Bridge checks completed. Run ./start.sh for an interactive Claude Code session.'
