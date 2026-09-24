# Brasil Lens — Backend & API

O **Brasil Lens** é uma plataforma interativa para visualização e análise de dados geográficos, socioeconômicos e ambientais do Brasil em diferentes níveis territoriais (país, regiões, estados e municípios).

Este repositório contém a **API principal e a camada de dados da solução**, responsável por coletar dados públicos (como IBGE, clima e queimadas), armazená-los de forma estruturada e disponibilizar endpoints rápidos para exibição no mapa interativo.

---

## Como a Aplicação Funciona

O propósito do sistema é permitir que qualquer pessoa explore indicadores públicos do Brasil de forma rápida e visual, sem sobrecarregar os servidores dos órgãos de dados governamentais.

```mermaid
flowchart LR
    subgraph SOURCES["Fontes de Dados Públicas"]
        direction TB
        IBGE["IBGE / SIDRA<br/>Malhas e Estatísticas"]
        CLIMA["Clima e Queimadas<br/>Open-Meteo · INPE · INMET"]
    end

    subgraph BACKEND["Brasil Lens — Backend"]
        direction TB
        INGEST["Jobs de Ingestão<br/>Normalização de Dados"]
        DB[("PostgreSQL + PostGIS<br/>Dados Espaciais e Séries")]
        API["API REST FastAPI<br/>Projeções e Cache Redis"]

        INGEST -->|"Gravação"| DB
        DB <-->|"Leitura otimizada"| API
    end

    subgraph CLIENT["Interface Web"]
        WEB["Mapa Interativo<br/>React + Leaflet"]
    end

    SOURCES -->|"Ingestão offline"| INGEST
    API -->|"GeoJSON com Indicadores"| WEB
    WEB -.->|"Salvar visualizações"| API

    classDef source fill:#f8fafc,stroke:#94a3b8,stroke-width:1.5px,color:#1e293b;
    classDef backend fill:#e0f2fe,stroke:#0284c7,stroke-width:1.5px,color:#0369a1;
    classDef db fill:#fef3c7,stroke:#d97706,stroke-width:1.5px,color:#92400e;
    classDef client fill:#dcfce7,stroke:#16a34a,stroke-width:1.5px,color:#15803d;

    class IBGE,CLIMA source;
    class INGEST,API backend;
    class DB db;
    class WEB client;
```

1. **Coleta de Dados:** O backend busca dados abertos de fontes oficiais (IBGE, Open-Meteo, INMET, CEMADEN, INPE) por meio de rotinas automatizadas e os organiza em um banco de dados geoespacial.
2. **Entrega Otimizada:** A API processa e entrega os mapas já acompanhados dos valores numéricos e das classes de cores, prontos para exibição.
3. **Interação do Usuário:** O usuário navega pelo mapa, consulta detalhes de cada município e pode salvar recortes favoritos para acessar depois.

---

## Estrutura de Containers (Docker)

Para garantir facilidade de implantação e avaliação:

- **Dockerfile do Backend:** Localizado na raiz deste repositório ([`Dockerfile`](Dockerfile)), configurado com Python 3.12 para executar a API FastAPI.
- **Docker Compose Completo:** Localizado na raiz deste repositório ([`docker-compose.yml`](docker-compose.yml)), responsável por iniciar todos os serviços integrados: Banco de Dados (PostgreSQL + PostGIS), Cache (Redis), API e a Interface Web (Frontend).

---

## Instruções de Instalação e Execução

### Opção 1: Execução com Docker e Docker Compose (Recomendado)

Esta é a maneira mais simples de rodar todo o ecossistema sem necessidade de instalar Python, Node.js ou PostgreSQL na sua máquina.

#### 1. Clonar o repositório
```bash
git clone https://github.com/henriquexaud/brasil-lens-backend.git
cd brasil-lens-backend
```

#### 2. Subir os serviços
```bash
docker compose up --build --wait
```
*Esse comando sobe o banco de dados, aplica as migrações automaticamente, inicia a API e inicializa a interface web.*

#### 3. Carregar os dados iniciais do IBGE
Para preencher o catálogo de indicadores e as malhas territoriais na primeira vez:
```bash
docker compose run --rm api python -m app.jobs.bootstrap
```
*(Dica: use a opção `--skip-municipal-geometries` no final do comando caso queira uma carga inicial rápida apenas com países e estados).*

#### 4. Acessar a aplicação

| Componente | Endereço | Descrição |
|---|---|---|
| **Interface Web** | [`http://localhost:5173`](http://localhost:5173) | Mapa e painel de controle |
| **Documentação da API (Swagger)** | [`http://localhost:8000/docs`](http://localhost:8000/docs) | Teste interativo dos endpoints |
| **Status da Aplicação (Healthcheck)** | [`http://localhost:8000/api/v1/health/ready`](http://localhost:8000/api/v1/health/ready) | Verificação de conexão com o banco |

Para encerrar a aplicação mantendo os dados salvos:
```bash
docker compose down
```

---

### Opção 2: Instalação Local (sem Docker)

Caso queira executar a API diretamente em seu ambiente de desenvolvimento:

#### 1. Pré-requisitos
- Python 3.12 ou superior instalado
- PostgreSQL 16 com extensão PostGIS ativa (você pode subir apenas o banco via `docker compose up -d db redis`)

#### 2. Criar ambiente virtual e instalar dependências
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install ".[dev]"
```

#### 3. Configurar ambiente e executar migrações
```bash
cp .env.example .env
export DATABASE_URL="postgresql+asyncpg://brasil_lens:brasil_lens@localhost:5432/brasil_lens"
alembic upgrade head
```

#### 4. Executar a ingestão e iniciar a API
```bash
python -m app.jobs.bootstrap --skip-municipal-geometries
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

---

### Atalhos Úteis com `Makefile`

Se você tiver o utilitário `make` instalado, pode utilizar comandos simplificados:

- `make up` — Inicia todos os serviços no Docker.
- `make dev` — Inicia a API com recarregamento automático a cada alteração de código.
- `make ingest` — Executa a carga completa de dados do IBGE.
- `make test` — Roda a suíte de testes automatizados com `pytest`.
- `make lint` — Executa a verificação de formatação e tipagem de código.
- `make smoke` — Executa testes de ponta a ponta no CRUD da API em execução.
- `make down` — Encerra os containers preservando os dados.

---

## Configuração (Variáveis de Ambiente)

O projeto já vem pronto para funcionar com valores padrão. Para alterar portas ou credenciais, copie o arquivo [`.env.example`](.env.example) para `.env`:

| Variável | Valor Padrão | Para que serve |
|---|---|---|
| `API_PORT` | `8000` | Porta local da API |
| `WEB_PORT` | `5173` | Porta local da interface web |
| `POSTGRES_PORT` | `5432` | Porta local do PostgreSQL |
| `POSTGRES_DB` | `brasil_lens` | Nome da base de dados |
| `REDIS_URL` | `redis://redis:6379/0` | Endereço do serviço de cache Redis |
| `CORS_ORIGINS` | `http://localhost:5173,...` | Origens autorizadas a acessar a API |

---

## Documentação Técnica Detalhada

As explicações técnicas aprofundadas, arquitetura e decisões de projeto estão organizadas na pasta [`docs/`](docs/):

- 🏛️ [**Arquitetura e Decisões de Design (`docs/ARCHITECTURE.md`)**](docs/ARCHITECTURE.md): Diagramas detalhados, escolhas de tecnologia e modelagem relacional/geoespacial.
- 📡 [**Referência da API REST (`docs/API_REFERENCE.md`)**](docs/API_REFERENCE.md): Contratos de todos os endpoints, formato GeoJSON e detalhes do CRUD de visualizações.
- 📥 [**Ingestão de Dados e Métricas (`docs/DATA_INGESTION.md`)**](docs/DATA_INGESTION.md): Tabela dos 14 indicadores, fontes do IBGE, rotinas e garantia de idempotência.
- 🌦️ [**Serviços de Clima e Queimadas (`docs/ENVIRONMENTAL_SERVICES.md`)**](docs/ENVIRONMENTAL_SERVICES.md): Integração com Open-Meteo, alertas INMET/CEMADEN, focos de calor INPE e cache Redis.
- 🛠️ [**Guia de Desenvolvimento e Extensão (`docs/DEVELOPMENT.md`)**](docs/DEVELOPMENT.md): Como adicionar novos indicadores e fontes, convenções de código e execução de testes.
