# Brasil Lens — Backend & API

Serviço de backend e API REST para a plataforma **Brasil Lens**, dedicada à exploração visual e analítica de dados geográficos, socioeconômicos e ambientais do Brasil em múltiplos recortes territoriais (país, regiões, estados e municípios).

Este repositório é o **componente principal da solução**: gerencia o banco relacional geoespacial (PostgreSQL + PostGIS), executa o pipeline de ingestão e normalização de dados abertos (IBGE, Open-Meteo, INMET, CEMADEN, INPE), fornece os endpoints em GeoJSON otimizado para o mapa interativo e mantém a camada de persistência para as visualizações salvas pelos usuários.

> **Importante para Avaliação:**
> - **Dockerfile do Backend:** Presente na raiz deste repositório ([`Dockerfile`](Dockerfile)), configurado em múltiplas camadas com Python 3.12-slim.
> - **Docker Compose da Solução:** Presente na raiz deste repositório ([`docker-compose.yml`](docker-compose.yml)), orquestrando toda a arquitetura: banco de dados PostGIS, cache Redis, a API FastAPI e o frontend web.

---

## Recursos e Destaques

- **Desacoplamento de Fontes Externas:** O cliente web nunca consome APIs externas diretamente. Os dados são ingeridos, consolidados e servidos pelo backend com alta previsibilidade e baixa latência.
- **GeoJSON Otimizado com Níveis de Detalhe (LOD):** Entrega geometrias pré-simplificadas (`overview` e `detail`) que reduzem em mais de 75% o peso da malha municipal sem perda visual.
- **Séries Históricas e Indicadores Derivados:** 14 indicadores socioeconômicos consolidados (população, PIB, renda, taxas de urbanização e densidades).
- **Camadas Ambientais em Tempo Real:** Clima e precipitação (Open-Meteo), focos de calor com densidade e WMS (INPE), riscos geo-hidrológicos (CEMADEN) e alertas meteorológicos (INMET).
- **Persistência de Visualizações:** CRUD completo (`/api/v1/views`) para salvar recortes analíticos personalizados.

---

## Instruções de Instalação e Execução

### Opção 1: Execução com Docker e Docker Compose (Recomendado)

O método mais direto e isolado. Não requer instalação local de Python ou PostgreSQL.

#### 1. Clonar o repositório
```bash
git clone https://github.com/henriquexaud/brasil-lens-backend.git
cd brasil-lens-backend
```

#### 2. Iniciar os serviços
```bash
docker compose up --build --wait
```
*Este comando inicializa o PostgreSQL/PostGIS, Redis, executa automaticamente as migrações do banco (Alembic), sobe a API FastAPI e inicia o container da interface web.*

#### 3. Carga inicial de dados (Ingestão do IBGE)
Na primeira execução, execute o bootstrap de dados para povoar o catálogo e as malhas territoriais:
```bash
docker compose run --rm api python -m app.jobs.bootstrap
```
*(Para uma carga rápida em desenvolvimento sem a malha de todos os municípios, adicione a flag `--skip-municipal-geometries`).*

#### 4. Endereços de Acesso

| Serviço | URL | Descrição |
|---|---|---|
| **Interface Web** | [`http://localhost:5173`](http://localhost:5173) | Aplicação web completa |
| **Documentação da API (Swagger)** | [`http://localhost:8000/docs`](http://localhost:8000/docs) | OpenAPI interativo |
| **Documentação da API (Redoc)** | [`http://localhost:8000/redoc`](http://localhost:8000/redoc) | Especificação técnica dos endpoints |
| **Healthcheck / Prontidão** | [`http://localhost:8000/api/v1/health/ready`](http://localhost:8000/api/v1/health/ready) | Status da API e conexão com banco |

Para encerrar os serviços preservando os dados: `docker compose down`.
Para encerrar removendo os volumes de banco: `docker compose down -v`.

---

### Opção 2: Desenvolvimento Local (sem Docker)

Para desenvolvedores que desejam executar o Python nativamente na máquina.

#### 1. Pré-requisitos
- Python 3.12 ou superior
- Instância do PostgreSQL 16+ com extensão PostGIS 3.4 ativa (pode ser iniciada via `docker compose up -d db redis`)

#### 2. Configurar o ambiente virtual e dependências
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install ".[dev]"
```

#### 3. Configurar variáveis de ambiente e banco
```bash
cp .env.example .env
export DATABASE_URL="postgresql+asyncpg://brasil_lens:brasil_lens@localhost:5432/brasil_lens"
alembic upgrade head
```

#### 4. Executar a ingestão inicial e iniciar o servidor
```bash
python -m app.jobs.bootstrap --skip-municipal-geometries
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

---

### Atalhos com `Makefile`

Para maior produtividade, disponibilizamos atalhos pré-configurados:

- `make up`: Inicializa todos os containers (banco, redis, api, frontend).
- `make dev`: Sobe a API com hot-reload ativo para desenvolvimento contínuo.
- `make ingest`: Dispara o pipeline de ingestão completa.
- `make test`: Executa a suíte de testes automatizados com `pytest`.
- `make lint`: Executa análise estática com `ruff` e checagem de tipos com `mypy`.
- `make check`: Roda testes, linter e verificações de integridade.
- `make smoke`: Testa todas as operações CRUD diretamente contra o servidor em execução.
- `make down`: Finaliza os containers preservando os dados.

---

## Configuração (Variáveis de Ambiente)

Todas as variáveis possuem valores padrão funcionais para desenvolvimento local. Caso deseje customizar portas ou credenciais, copie o arquivo [`.env.example`](.env.example) para `.env`:

| Variável | Valor Padrão | Descrição |
|---|---|---|
| `API_PORT` | `8000` | Porta local da API |
| `WEB_PORT` | `5173` | Porta local da interface web |
| `POSTGRES_PORT` | `5432` | Porta local do banco PostgreSQL |
| `POSTGRES_DB` | `brasil_lens` | Nome da base de dados |
| `REDIS_URL` | `redis://redis:6379/0` | Conexão com o serviço Redis de cache |
| `CORS_ORIGINS` | `http://localhost:5173,...` | Origens autorizadas para requisições cross-origin |
| `READ_CACHE_TTL_SECONDS`| `300` | Tempo de cache em memória para consultas do mapa |

---

## Documentação Técnica Aprofundada

Para detalhes de arquitetura, contratos de API e regras de negócio, consulte a pasta [`docs/`](docs/):

- 🏛️ [**Arquitetura e Decisões de Design (`docs/ARCHITECTURE.md`)**](docs/ARCHITECTURE.md): Diagramas de fluxo, trade-offs técnicos e modelagem relacional/geoespacial.
- 📡 [**Referência da API REST (`docs/API_REFERENCE.md`)**](docs/API_REFERENCE.md): Contratos de entrada e saída, paginação, GeoJSON enriquecido e endpoints de visualizações salvas.
- 📥 [**Ingestão de Dados e Métricas (`docs/DATA_INGESTION.md`)**](docs/DATA_INGESTION.md): Catálogo completo dos 14 indicadores, fontes do IBGE, jobs CLI e idempotência.
- 🌦️ [**Serviços Ambientais e Clima (`docs/ENVIRONMENTAL_SERVICES.md`)**](docs/ENVIRONMENTAL_SERVICES.md): Integração Open-Meteo, INPE Queimadas, alertas CEMADEN/INMET e cache Redis.
- 🛠️ [**Guia de Desenvolvimento e Extensão (`docs/DEVELOPMENT.md`)**](docs/DEVELOPMENT.md): Como adicionar novos indicadores, novos provedores de dados, convenções de código e testes.
