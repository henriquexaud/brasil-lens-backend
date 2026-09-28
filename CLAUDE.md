# Backend — guia para agentes

FastAPI + SQLAlchemy async + GeoAlchemy2 sobre PostgreSQL 16/PostGIS 3.4, Redis opcional. Python 3.12. Regras gerais do projeto (direção, stack compartilhada, cotas) estão no `CLAUDE.md` da pasta acima, quando existir.

## Camadas

Fluxo em um sentido só: `api/v1` → `services` → `repositories` / `providers`.

- `api/v1/`: rotas finas. Validam parâmetros (`Annotated`, `Path(pattern=...)`), escolhem sessão (`get_session` / `get_write_session`), definem headers de cache e delegam ao service. Sem SQL nem regra de negócio aqui.
- `services/`: regras, agregação, interpolação, cache em memória (`TTLCache`) e Redis (`core/redis_cache`).
- `repositories/`: consultas SQL/PostGIS. Nada de HTTP externo.
- `providers/`: adaptadores de fontes externas. Usam `providers/base.http_client` (retry e backoff) e convertem para registros internos; o formato cru da fonte nunca sai daqui.
- `schemas/`: contratos Pydantic das respostas; o frontend depende deles (camelCase onde já é camelCase).
- `jobs/`: ingestão e agendador. `core/`: config, cache, erros, logging.

Erros de domínio: lance subclasses de `core/errors.DomainError` (mensagem em pt-BR, `code` estável, detalhes nomeados). Não devolva `HTTPException` solta nem dicionários de erro ad hoc.

## Regras

- Schema muda só por **nova** migration Alembic em `alembic/versions/` (sequência `00NN_...`). Nunca edite migration existente.
- Preserve `ibge_code`, hierarquia `territories.parent_id` e as geometrias `overview`/`detail`: todas as camadas ambientais dependem delas.
- Nova fonte externa: adaptador em `providers/`, timeout e cache com TTL, falha vira `ProviderError` (a API degrada, não trava). Credencial, se houver, via `core/config.py` + variável de ambiente.
- Mudança de contrato de cache: o namespace está em `redis_cache_prefix` (`brasil-lens:v4`); chaves antigas expiram por TTL, sem varredura.
- Tipagem estrita: `mypy` com `disallow_untyped_defs`. Ruff com linha de 100.
- `DeprecationWarning` vinda de `app.*` quebra os testes (`filterwarnings = error`).

## Fatos das fontes

- INPE (focos): a consulta WFS de identificação devolve os N registros **mais recentes** da caixa, não os mais próximos; mantenha a caixa pequena ou busque a mais e ordene por distância. ~78 % das detecções são VIIRS 375 m e ~16 % GOES-19, que repete o mesmo pixel a cada ~10 min (cuidado ao contar). CSV com colunas `latitude`/`longitude`.
- INPE GeoServer responde 414 acima de ~8 KB de URL. O SLD aceita `env('wms_scale_denominator')`, `Recode`, filtros e `ElseFilter`.
- Open-Meteo: uma requisição consulta vários pontos; o estado consulta ~20 municípios reais e interpola o resto (`services/spatial_interpolation.py`) para poupar a cota diária.

## Testes

- Sem acesso à rede: fixtures em `tests/fixtures` + `respx`. Testes com banco levam `pytest.mark.db` e fazem rollback.
- Testes de API com banco devem sobrescrever `get_session` com a sessão do teste (o pool global pertence a outro event loop). Modelo: `_api()` em `tests/test_map_queries.py`.
- Regressões corrigidas ganham teste (há `tests/test_bug_hunt_regressions.py`).

## Rodar as verificações

Não há Python 3.12 no host. Use um container descartável com o código montado, na rede da stack:

```sh
docker run --rm --network brasil-lens_default -v "$PWD":/app -w /app \
  -e DATABASE_URL=postgresql+asyncpg://brasil_lens:brasil_lens@db:5432/brasil_lens \
  -e WEATHER_REFRESH_ENABLED=false -e REDIS_CACHE_PREFIX=bl-test:v4 \
  brasil-lens-api-dev:latest sh -c "pytest -q && ruff check app tests && ruff format --check app tests && mypy app"
```

Scripts avulsos no container precisam de `-e PYTHONPATH=/app`.

`make test`, `make lint` e `make check` também funcionam, mas `make dev`, `make up`, `make down` e `make reset` mexem na stack compartilhada (mesmo nome de projeto `brasil-lens`) — não use sem pedido.
