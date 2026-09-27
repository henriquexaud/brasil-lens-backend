# Brasil Lens API

API de clima e meio ambiente do Brasil. Combina geografia do IBGE com condições e previsão meteorológica, alertas, focos de calor e hidrografia. O processamento inclui localização espacial, agregação por território, interpolação meteorológica e densidade de focos por área.

## Componentes e dependências

A aplicação tem três componentes que se comunicam por HTTP: [frontend React](https://github.com/henriquexaud/brasil-lens-frontend), esta API FastAPI e APIs públicas externas. PostgreSQL 16 com PostGIS 3.4 persiste geografia, dados ambientais e municípios acompanhados; Redis 7.4 complementa o cache local e pode ficar indisponível sem impedir as consultas.

O IBGE fornece territórios e malhas por APIs REST públicas e gratuitas. Os jobs processam esses dados e os gravam no PostGIS; o frontend recebe GeoJSON desta API. Open-Meteo, INMET, CEMADEN, INPE e ANA complementam a experiência ambiental. Veja a [arquitetura e integrações](docs/ARCHITECTURE.md).

## Instalação com Docker

Requisitos: Git, Docker e Docker Compose v2. Não é necessário instalar Python, Node ou banco no host.

```sh
git clone https://github.com/henriquexaud/brasil-lens-backend.git
cd brasil-lens-backend
docker compose up --build --wait
docker compose run --rm api python -m app.jobs.bootstrap
```

O Compose deste repositório sobe banco, Redis, API e frontend, cujo código é baixado do repositório público durante o build. As migrations rodam antes da API. A ingestão inicial é necessária para preencher o mapa e pode demorar conforme a disponibilidade do IBGE. Para importar apenas malhas nacionais, regionais e estaduais, acrescente `--skip-municipal-geometries`; para limitar malhas municipais, use `--states 35,31`.

- Interface: `http://localhost:5173`
- API e Swagger: `http://localhost:8000/docs`
- Apenas backend e dependências: `docker compose up --build --wait db redis api`
- Parar preservando dados: `docker compose down`

## Configuração

A stack funciona sem `.env`. Para personalizar, copie `.env.example` para `.env`:

| Variável | Uso |
|---|---|
| `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`, `POSTGRES_PORT` | Banco e porta publicada |
| `API_PORT`, `WEB_PORT` | Portas da API e interface |
| `FRONTEND_CONTEXT` | Origem do build web; por padrão, GitHub. Use `../frontend` para um clone local |
| `VITE_API_BASE_URL` | URL no bundle; `/api/v1` usa o proxy Nginx da stack. Requer rebuild |
| `CORS_ORIGINS` | Origens permitidas quando o navegador acessa a API diretamente |
| `REDIS_URL`, `READ_CACHE_*`, `HTTP_CACHE_MAX_AGE` | Cache compartilhado, local e HTTP |
| `IBGE_*`, `INPE_QUEIMADAS_*`, `FIRE_HOTSPOTS_CACHE_TTL_SECONDS` | Fontes externas, timeouts e cache de focos |
| `GEOMETRY_OVERVIEW_TOLERANCE`, `GEOMETRY_DETAIL_TOLERANCE` | Simplificação na próxima ingestão: `0.005` e `0.001` graus |
| `WEATHER_REFRESH_ENABLED`, `HYDROGRAPHY_WARMUP_ENABLED` | Atualização meteorológica e aquecimento da hidrografia; `false` desliga |

No Compose, `DATABASE_URL` é montada a partir de `POSTGRES_*`. Para execução direta no host, a aplicação lê `DATABASE_URL` do ambiente/`.env`; `REDIS_URL` deve apontar para um Redis acessível pelo host ou ficar vazia para usar apenas cache local. Os parâmetros Python adicionais estão em `app/core/config.py`.

## Operações da interface

| Método | Rota | Funcionalidade |
|---|---|---|
| GET | `/api/v1/map` | Explora a malha territorial |
| GET | `/api/v1/me/followed-municipalities` | Lista municípios acompanhados |
| POST | `/api/v1/territories/locate` | Localiza município pelas coordenadas |
| PUT | `/api/v1/me/followed-municipalities/{code}` | Acompanha município |
| DELETE | `/api/v1/me/followed-municipalities/{code}` | Deixa de acompanhar município |
| POST | `/api/v1/me/followed-municipalities/{code}/notifications` | Configura avisos no acompanhamento |

O MVP usa um usuário fixo `local`, sem autenticação; a lista de municípios acompanhados é compartilhada nesta instalação. A preferência de avisos é persistida, mas não há envio de notificações por push, e-mail ou outro canal. Os quatro métodos são consumidos pelo frontend. Há também rotas de clima, previsão, alertas, hidrografia e focos. Veja a [referência da API](docs/API_REFERENCE.md); os contratos completos ficam no Swagger.

## Desenvolvimento e verificação

```sh
make dev
make test
make lint
make smoke
```

O Dockerfile tem targets `production` (padrão, dependências de execução) e `development` (inclui pytest, Ruff, mypy e testes). O Compose de desenvolvimento seleciona esse target, monta o código e habilita recarga automática; os comandos `make` constroem a imagem adequada.

Para trabalhar sem container de API, instale Python 3.12 (versão usada e validada no Docker) e mantenha PostgreSQL/PostGIS disponível:

```sh
python3.12 -m venv .venv
. .venv/bin/activate
pip install -e '.[dev]'
alembic upgrade head
uvicorn app.main:app --reload
```

A suíte inclui testes unitários e testes `db` com PostgreSQL/PostGIS; alguns exigem territórios ingeridos. `pytest -q -m 'not db'` executa somente os testes sem banco. A [documentação de desenvolvimento](docs/DEVELOPMENT.md) e o [guia de ingestão](docs/DATA_INGESTION.md) detalham esses fluxos.

A [revisão do projeto](docs/PROJECT_REVIEW.md) reúne diagnóstico, prioridades, matriz de requisitos do MVP e resultados de validação.
