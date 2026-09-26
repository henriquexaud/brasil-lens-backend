#!/usr/bin/env bash
# Confere que a API está pronta e que o mapa entrega o recorte territorial.
set -euo pipefail

API="${1:-${API_BASE_URL:-http://localhost:8000/api/v1}}"
BODY="$(mktemp)"
trap 'rm -f "$BODY"' EXIT

fail() { printf '\033[31m✗ %s\033[0m\n' "$1" >&2; exit 1; }
ok() { printf '\033[32m✓\033[0m %s\n' "$1"; }

status() { curl -s -o "$BODY" -w '%{http_code}' "$API$1"; }
field() { python3 -c "import json; print(json.load(open('$BODY'))$1)"; }

[[ "$(status /health/ready)" == "200" ]] || fail "API indisponível"
ok "GET /health/ready → 200"

[[ "$(status '/map?level=state&lod=overview')" == "200" ]] || fail "GET /map falhou"
ok "GET /map?level=state → 200 ($(field "['scope']['count']") unidades federativas)"

printf '\nAPI e camada geográfica estão disponíveis.\n'
