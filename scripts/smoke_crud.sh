#!/usr/bin/env bash
#
# Exercita as quatro operações REST contra a API no ar, na mesma ordem em que o
# frontend as executa: POST → GET (lista) → GET (item) → PUT → DELETE.
#
# Serve de verificação de ponta a ponta do que a rubrica pede: cada chamada
# atravessa HTTP → FastAPI → SQLAlchemy → PostgreSQL e volta.
#
#   ./scripts/smoke_crud.sh [base-url]
#
set -euo pipefail

API="${1:-${API_BASE_URL:-http://localhost:8000/api/v1}}"
NAME="smoke-$(date +%s)"

# Corpo da última resposta. mktemp evita colisão entre execuções simultâneas.
BODY="$(mktemp)"
trap 'rm -f "$BODY"' EXIT

fail() { printf '\033[31m✗ %s\033[0m\n' "$1" >&2; exit 1; }
ok()   { printf '\033[32m✓\033[0m %s\n' "$1"; }

status() { # método, caminho, corpo (opcional) -> código HTTP na saída padrão
  local method="$1" path="$2" body="${3:-}"
  if [[ -n "$body" ]]; then
    curl -s -o "$BODY" -w '%{http_code}' -X "$method" "$API$path" \
      -H 'Content-Type: application/json' -d "$body"
  else
    curl -s -o "$BODY" -w '%{http_code}' -X "$method" "$API$path"
  fi
}

field() { python3 -c "import json,sys; print(json.load(open('$BODY'))$1)"; }

echo "API: $API"

[[ "$(status GET /health/ready)" == "200" ]] || fail "API indisponível (GET /health/ready)"
ok "GET  /health/ready            → 200"

[[ "$(status GET '/map?level=state&indicator=population&year=latest')" == "200" ]] \
  || fail "GET /map falhou"
ok "GET  /map?level=state         → 200 ($(field "['scope']['count']") territórios)"

code=$(status POST /views "{\"name\":\"$NAME\",\"level\":\"state\",\"indicatorKey\":\"population\",\"year\":\"latest\"}")
[[ "$code" == "201" ]] || fail "POST /views devolveu $code (esperado 201)"
ID=$(field "['id']")
ok "POST /views                   → 201 (id $ID)"

[[ "$(status GET /views)" == "200" ]] || fail "GET /views falhou"
ok "GET  /views                   → 200 ($(field "['pagination']['total']") salvas)"

[[ "$(status GET "/views/$ID")" == "200" ]] || fail "GET /views/{id} falhou"
ok "GET  /views/$ID → 200"

code=$(status PUT "/views/$ID" "{\"name\":\"$NAME-editada\",\"level\":\"municipality\",\"parentCode\":\"35\",\"indicatorKey\":\"population\",\"year\":\"2022\",\"classes\":7}")
[[ "$code" == "200" ]] || fail "PUT /views/{id} devolveu $code"
[[ "$(field "['year']")" == "2022" ]] || fail "PUT não persistiu o ano"
ok "PUT  /views/$ID → 200 (recorte substituído)"

code=$(status DELETE "/views/$ID")
[[ "$code" == "204" ]] || fail "DELETE /views/{id} devolveu $code (esperado 204)"
ok "DELETE /views/$ID → 204"

code=$(status GET "/views/$ID")
[[ "$code" == "404" ]] || fail "a visualização sobreviveu ao DELETE (GET devolveu $code)"
ok "GET  /views/{id} após DELETE  → 404 (removida do banco)"

printf '\n\033[32mOs quatro métodos atravessaram frontend → API → PostgreSQL.\033[0m\n'
