#!/usr/bin/env bash
set -euo pipefail

IMAGE="${PKMCP_IMAGE:-project-knowledge-mcp:ci}"
TOKEN="${MCP_AUTH_TOKEN:-ci-token}"
DIRECT_PORT="${PKMCP_DIRECT_PORT:-18080}"
CADDY_PORT="${PKMCP_CADDY_PORT:-18081}"
NETWORK="pkmcp-ci-${RANDOM}-${RANDOM}"
APP_CONTAINER="pkmcp-ci-app-${RANDOM}-${RANDOM}"
CADDY_CONTAINER="pkmcp-ci-caddy-${RANDOM}-${RANDOM}"

cleanup() {
  docker rm -f "$CADDY_CONTAINER" "$APP_CONTAINER" >/dev/null 2>&1 || true
  docker network rm "$NETWORK" >/dev/null 2>&1 || true
}
trap cleanup EXIT

wait_for_registry() {
  local url="$1"
  local token_arg=()
  if [[ "${2:-}" != "" ]]; then
    token_arg=(--token "$2")
  fi
  for _ in $(seq 1 80); do
    if python scripts/ci/smoke_mcp_registry.py "$url" "${token_arg[@]}" >/tmp/pkmcp-registry-smoke.log 2>&1; then
      cat /tmp/pkmcp-registry-smoke.log
      return 0
    fi
    sleep 0.25
  done
  cat /tmp/pkmcp-registry-smoke.log >&2 || true
  return 1
}

require_http_status() {
  local expected="$1"
  local url="$2"
  shift 2
  local actual
  actual=$(curl -sS -o /tmp/pkmcp-curl-body -w "%{http_code}" "$@" "$url")
  if [[ "$actual" != "$expected" ]]; then
    echo "Expected HTTP $expected from $url but got $actual" >&2
    cat /tmp/pkmcp-curl-body >&2 || true
    return 1
  fi
}

echo "::group::container CLI smoke"
docker run --rm project-knowledge-mcp:ci --help >/tmp/pkmcp-help.txt
docker run --rm project-knowledge-mcp:ci start --help >/tmp/pkmcp-start-help.txt
docker run --rm project-knowledge-mcp:ci setup-ui --help >/tmp/pkmcp-setup-ui-help.txt
echo "container CLI smoke passed"
echo "::endgroup::"

echo "::group::Caddyfile validation"
docker run --rm \
  -v "$PWD/deploy/Caddyfile.example:/etc/caddy/Caddyfile:ro" \
  -e MCP_AUTH_TOKEN=ci-token \
  -e PKMCP_SITE_ADDRESS=:8080 \
  -e PKMCP_UPSTREAM=127.0.0.1:8000 \
  caddy:2-alpine caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
echo "Caddyfile validation passed"
echo "::endgroup::"

echo "::group::container StreamableHTTP and real Caddy bearer gate smoke"
docker network create "$NETWORK" >/dev/null

docker run -d \
  --name "$APP_CONTAINER" \
  --network "$NETWORK" \
  -p "127.0.0.1:${DIRECT_PORT}:8000" \
  -e PROJECT_KNOWLEDGE_CONFIG=/workspace/project.example.yaml \
  "$IMAGE" \
  start --transport streamable-http --host 0.0.0.0 --port 8000 >/dev/null

wait_for_registry "http://127.0.0.1:${DIRECT_PORT}/mcp"

docker run -d \
  --name "$CADDY_CONTAINER" \
  --network "$NETWORK" \
  -p "127.0.0.1:${CADDY_PORT}:8080" \
  -v "$PWD/deploy/Caddyfile.example:/etc/caddy/Caddyfile:ro" \
  -e MCP_AUTH_TOKEN=ci-token \
  -e PKMCP_SITE_ADDRESS=:8080 \
  -e PKMCP_UPSTREAM="$APP_CONTAINER:8000" \
  caddy:2-alpine caddy run --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null

for _ in $(seq 1 80); do
  if [[ "$(curl -sS -o /tmp/pkmcp-curl-body -w "%{http_code}" "http://127.0.0.1:${CADDY_PORT}/mcp" 2>/dev/null || true)" == "401" ]]; then
    break
  fi
  sleep 0.25
done
require_http_status 401 "http://127.0.0.1:${CADDY_PORT}/mcp"
require_http_status 401 "http://127.0.0.1:${CADDY_PORT}/mcp" -H "Authorization: Bearer wrong-token"
wait_for_registry "http://127.0.0.1:${CADDY_PORT}/mcp" "$TOKEN"
echo "remote bridge bearer gate and registry parity smoke passed"
echo "::endgroup::"
