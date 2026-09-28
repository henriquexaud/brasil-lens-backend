# Arquitetura

## Produto

Clima e meio ambiente do Brasil num mapa (país → UF → município): clima atual e previsão de 3 dias, chuva em 48 h, alertas INMET/CEMADEN, focos do INPE, hidrografia da ANA, busca, localização e municípios acompanhados. É um produto em evolução, de dono único.

Princípios:
1. **Dados honestos:** estimado aparece marcado, antigo aparece marcado e o que é duvidoso não aparece.
2. **Interface leve e estável:** detalhes são para o frontend (`frontend/docs/ui.md`).
3. **Falha isolada:** uma fonte fora do ar degrada só a própria camada.
4. **Cota respeitada:** amostrar e cachear antes de consultar a fonte.
5. **Simplicidade:** nada construído "para o futuro".

Limitações atuais: não há autenticação (tudo roda como o usuário `local`); a preferência de avisos é salva, mas nada é enviado; as estações do INMET não têm consumidor.

## Componentes

O navegador fala com a API pelo nginx (`/api/v1`). A API, em FastAPI, usa PostGIS, Redis (opcional) e as fontes IBGE, Open-Meteo, INMET, CEMADEN, INPE e ANA. O navegador só acessa terceiros diretamente para tiles: o mapa base da Esri e o WMS do INPE, cuja URL e filtro vêm da API.

Camadas do backend, com dependência em um só sentido: `api/v1` → `services` → `repositories` (SQL/PostGIS) | `providers` (HTTP e normalização). Apoio: `schemas` (contratos), `jobs` (ingestão e agendador), `core` (config, erros, cache, cooldown). O `lifespan` de `main.py` inicia o agendador de alertas (a cada 10 min) e aquece as áreas municipais e a hidrografia.

Onde fica o estado: PostGIS guarda território, alertas, municípios acompanhados e proveniência; Redis guarda derivados compartilhados; a memória do processo guarda caches TTL, cooldowns e requisições em voo; o navegador guarda o cache do TanStack Query e o `sessionStorage`.

## Contrato HTTP

- Rotas em `app/api/v1/router.py`; o contrato completo está no Swagger (`/docs`). JSON em camelCase (`CamelModel`), espelhado em `frontend/src/api/types.ts`.
- Formato de erro: `{"error": {"code", "message", "details"}}`, com mensagem em pt-BR. Códigos: `invalid_parameter` 400, `not_found` 404, `conflict` 409, validação 422, `provider_error` 502, `provider_rate_limited` 503 (com `details.retryAfterSeconds`).
- O payload das fontes externas traz `status`: `ok`, `stale` (vindo do fallback) ou `partial`. Quem consome deve mostrar esse status.
- `/map` responde com ETag e `max-age=3600, stale-while-revalidate=86400`; as demais rotas geográficas, com `max-age=300`; as de acompanhamento, com `no-store`. Nas rotas de clima, `force=true` busca de novo leituras com mais de 5 min.
- Acompanhamento (`/me/followed-municipalities/{code}`): PUT responde 201 com `Location` na primeira vez e 200 se já seguia; DELETE responde 204; POST `.../notifications` liga ou desliga os avisos (404 se o município não é seguido). O usuário vem de `api/deps.get_current_user_id`, o único ponto a trocar quando houver autenticação.

## Cache e degradação

- Duas camadas: `core/cache.TTLCache` (memória, por processo) e `core/redis_cache` (opcional; payload zlib, chave `brasil-lens:v4:<namespace>:<sha256>`). Falha do Redis é tratada como miss e pausa o uso dele por 30 s.
- Todo derivado da geografia leva `data_version` (a última ingestão territorial) na chave; uma nova ingestão invalida tudo sem varredura.
- Cada fonte tem um `SourceCooldown` (60 s). O fallback tem limite de idade e sai marcado. Buscas em voo são compartilhadas por chave (dedup e locks).
- Ao mudar um cache: payload incompatível pede namespace novo (ex.: `weather-reading-v2`); mudança de frescor precisa ser feita também no frontend. Os TTLs de cada domínio estão no doc do domínio e no código.

## Fluxos

1. **Clima.** No Brasil, `/weather/current?forecast=false` (capitais) pinta primeiro e `/weather/states` refina. Numa UF, `/weather/state?parent=`; `/weather/municipalities`, paginado, só entra se a anterior falhar. Com zoom ≥ 8, `/weather/viewport`. Ao selecionar, `/weather/current?territory=`.
2. **Focos.** `/fire-hotspots` traz a metadata; o WMS usa o `cqlFilter` dela; `/summary` e `/identify` recebem `at = metadata.windowEnd`.
3. **Alertas.** O agendador grava em `weather_alerts`; o frontend consulta `/weather/alerts` a cada 90 s.
4. **Falha de fonte.** A fonte entra em cooldown; a API devolve `stale` ou um erro com `code`, e o frontend pausa só aquela fonte.
5. **Localizar.** `POST /territories/locate` → `ST_Covers` na malha canônica.
6. **Nova ingestão.** Muda o `data_version`, os caches passam a usar chaves novas e o ETag de `/map` muda.

Domínios: [clima](weather.md) · [alertas](alerts.md) · [focos](fire.md) · [geografia](geography.md) · Regras: [invariantes](invariants.md) · Porquês: [decisões](decisions.md)
