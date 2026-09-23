# Brasil Lens — backend

Exploração visual de dados geográficos, demográficos e econômicos do Brasil em um
mapa interativo: Brasil → região → estado → município.

Este é o **repositório principal** do projeto: API (FastAPI), banco
(PostgreSQL/PostGIS), ingestão dos dados públicos do IBGE e o
`docker-compose.yml` que sobe a aplicação inteira. A interface web vive em um
repositório próprio:

| Repositório | Conteúdo |
|---|---|
| **brasil-lens-backend** (este) | API REST, banco, ingestão, compose da aplicação completa |
| [brasil-lens-frontend](https://github.com/henriquexaud/brasil-lens-frontend) | React + Vite + Leaflet — o mapa |

**Princípio central da arquitetura:** o frontend nunca fala com o IBGE. Dados
públicos entram por jobs de ingestão offline, são normalizados e persistidos em
PostgreSQL/PostGIS, e a API serve projeções de leitura já prontas para o mapa.
Nenhuma requisição de usuário depende da disponibilidade ou latência de uma
fonte externa.

---

## Como rodar

Pré-requisito único: **Docker** (com Docker Compose v2). Não é preciso instalar
Python, Node nem PostgreSQL — e não é preciso clonar o frontend: o compose o
constrói direto do repositório dele.

```bash
git clone https://github.com/henriquexaud/brasil-lens-backend.git
cd brasil-lens-backend

docker compose up --build --wait                           # banco + API + frontend
docker compose run --rm api python -m app.jobs.bootstrap    # dados do IBGE (~10 min, só na 1ª vez)
```

Pronto:

| O quê | Onde |
|---|---|
| Aplicação | <http://localhost:5173> |
| API (Swagger/OpenAPI) | <http://localhost:8000/docs> |
| Healthcheck | <http://localhost:8000/api/v1/health/ready> |

As migrations rodam sozinhas quando a API sobe, e o `--wait` só devolve o
terminal quando ela está saudável — então a ingestão nunca encontra o schema pela
metade. A ingestão é o único passo manual, de propósito: ela baixa os dados das
APIs do IBGE, e é bom vê-la terminar.

Se ela terminar avisando que alguma etapa ficou `partial`, é instabilidade da
rede do IBGE: rode o mesmo comando de novo. A ingestão é idempotente — o que já
foi gravado não muda, e o que faltou entra.

Para parar: `docker compose down` (mantém os dados) ou `docker compose down -v`
(apaga também o banco).

### Variações

```bash
# Só o backend (banco + API), sem o frontend
docker compose up --build --wait db api

# Frontend construído de uma cópia local, em vez do GitHub
git clone https://github.com/henriquexaud/brasil-lens-frontend.git ../brasil-lens-frontend
FRONTEND_CONTEXT=../brasil-lens-frontend docker compose up --build --wait

# Ingestão sem a malha municipal (mais leve; o mapa municipal fica sem polígonos)
docker compose run --rm api python -m app.jobs.bootstrap --skip-municipal-geometries
```

Nenhuma variável de ambiente é obrigatória. Para mudar portas, credenciais ou a
origem do frontend, copie `.env.example` para `.env` e ajuste (ver
[Variáveis de ambiente](#variáveis-de-ambiente)).

### Atalhos com `make`

Os mesmos comandos, mais curtos. Opcional — tudo acima funciona sem `make`.

| Comando | O que faz |
|---|---|
| `make up` | sobe banco + API + frontend |
| `make up-api` | sobe só banco + API |
| `make ingest` / `make ingest-quick` | ingestão completa / sem geometrias municipais |
| `make down` | derruba os containers, preservando o banco |
| `make dev` | sobe tudo com recarga automática da API (`uvicorn --reload`) |
| `make logs` | acompanha os logs da API |
| `make test` | 91 testes (unitários + integração com banco) |
| `make lint` / `make format` | ruff + ruff format + mypy / formatação |
| `make check` | testes + lint — o que um CI checaria |
| `make smoke` | exercita `GET`, `POST`, `PUT` e `DELETE` contra a API no ar |
| `make migrate` / `make seed` | migrations / só o catálogo de indicadores |
| `make psql` | abre um `psql` no banco |
| `make reset` | apaga o banco e sobe tudo de novo |

### Desenvolvimento com recarga automática

`make up` usa a imagem construída do `Dockerfile`. Para editar o código e ver o
resultado sem reconstruir:

```bash
make dev    # = docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build --wait
```

A sobreposição monta este repositório dentro do container da API e troca o
comando por `uvicorn --reload`. O frontend tem a sua própria recarga
(`npm run dev`), no repositório dele.

### Sem Docker

Requer Python 3.12+ e um PostgreSQL 14+ com PostGIS 3 no ar (o do compose serve:
`docker compose up -d db`).

```bash
python -m venv .venv && source .venv/bin/activate
pip install ".[dev]"

export DATABASE_URL="postgresql+asyncpg://brasil_lens:brasil_lens@localhost:5432/brasil_lens"
alembic upgrade head
python -m app.jobs.bootstrap
uvicorn app.main:app --reload
```

---

## Arquitetura

```mermaid
flowchart TD
    IBGE["<b>IBGE / SIDRA</b> — APIs públicas<br/>Localidades v1 · Malhas v3 · Agregados v3"]
    PROV["<b>FastAPI · providers/ibge</b><br/>adapta o formato externo para tipos internos"]
    JOBS["<b>FastAPI · jobs/</b> — ingestão offline<br/>valida, normaliza, deriva, faz upsert idempotente"]
    DB[("<b>PostgreSQL 16 + PostGIS 3.4</b><br/>territórios · geometrias por LOD · valores por ano · visualizações")]
    SVC["<b>FastAPI · repositories/ + services/</b><br/>SQL e PostGIS · latest · derivação · classificação"]
    REST["<b>API REST</b> — /api/v1<br/>GET · POST · PUT · DELETE"]
    WEB["<b>brasil-lens-frontend</b> — React 18 + Vite + Leaflet<br/>coropleta · filtros · drill-down · visualizações salvas"]

    IBGE -- "HTTPS, só durante a ingestão" --> PROV
    PROV --> JOBS
    JOBS -- "UPSERT idempotente" --> DB
    DB --> SVC
    SVC --> REST
    REST -- "GeoJSON + metadados de coropleta" --> WEB
    WEB -- "escrita: POST · PUT · DELETE /views" --> REST
    IBGE -. "o browser nunca fala com o IBGE" .-x WEB

    classDef ext fill:#eef4fb,stroke:#1d6fa5,color:#12202c
    classDef svc fill:#f4f5f9,stroke:#5d6875,color:#12202c
    classDef store fill:#f6f2e8,stroke:#9a7b34,color:#12202c
    classDef ui fill:#eef7f0,stroke:#2f7a4d,color:#12202c
    class IBGE ext
    class PROV,JOBS,SVC,REST svc
    class DB store
    class WEB ui
```

As decisões arquiteturais, incluindo as alternativas recusadas, estão em
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

### Componentes

| Serviço do compose | Tecnologia | Responsabilidade | Origem |
|---|---|---|---|
| `db` | PostgreSQL 16 + PostGIS 3.4 | territórios, geometrias, séries e visualizações | imagem `postgis/postgis:16-3.4` |
| `api` | FastAPI · SQLAlchemy 2 · asyncpg · Alembic | ingestão, projeções de leitura, CRUD de visualizações | `Dockerfile` deste repositório |
| `web` | React 18 · Vite 6 · TanStack Query · Leaflet | mapa coroplético, filtros e escrita das visualizações | `Dockerfile` do [frontend](https://github.com/henriquexaud/brasil-lens-frontend) |

### Dependências

| Parte | Runtime | Principais dependências | Manifesto |
|---|---|---|---|
| API | Python 3.12 | FastAPI 0.115 · SQLAlchemy 2.0 · asyncpg · GeoAlchemy2 · Alembic · httpx · orjson · Pydantic 2 | [`pyproject.toml`](pyproject.toml) |
| Frontend | Node 22 | React 18 · Vite 6 · TanStack Query 5 · Leaflet 1.9 · react-leaflet 4 · TypeScript 5.7 | `package.json` do frontend |
| Banco | — | PostgreSQL 16 + PostGIS 3.4 | [`docker-compose.yml`](docker-compose.yml) |

As versões estão fixadas nos manifestos. O `Dockerfile` da API extrai as
dependências do próprio `pyproject.toml`, então não há `requirements.txt` para
sair de sincronia.

### Variáveis de ambiente

Todas têm default no `docker-compose.yml`; o `.env` só serve para mudar algo.
As que importam:

| Variável | Default | Para quê |
|---|---|---|
| `FRONTEND_CONTEXT` | repositório do frontend no GitHub | de onde o serviço `web` é construído (URL git ou pasta local) |
| `VITE_API_BASE_URL` | `http://localhost:8000/api/v1` | URL da API vista pelo navegador, **assada no bundle** em tempo de build |
| `API_PORT` / `WEB_PORT` / `POSTGRES_PORT` | `8000` / `5173` / `5432` | portas publicadas no host |
| `CORS_ORIGINS` | `http://localhost:5173,http://127.0.0.1:5173` | origens que podem chamar a API |
| `POSTGRES_USER` / `_PASSWORD` / `_DB` | `brasil_lens` | credenciais do banco |
| `IBGE_BASE_URL` | `https://servicodados.ibge.gov.br` | raiz das APIs do IBGE |
| `READ_CACHE_TTL_SECONDS` | `300` | TTL do cache em processo das projeções de leitura |

A lista completa, comentada, está em [.env.example](.env.example).

---

## Ingestão

Quatro jobs, todos idempotentes e todos registrados na tabela `ingestion_runs`.
Os comandos abaixo rodam dentro do container (prefixe com
`docker compose run --rm api`) ou direto no host, fora do Docker:

```bash
python -m app.jobs.seed_indicators      # catálogo de indicadores
python -m app.jobs.import_territories   # Brasil, regiões, UFs, municípios
python -m app.jobs.import_geometries    # malhas + LODs de visualização
python -m app.jobs.import_indicators    # valores + recálculo dos derivados
python -m app.jobs.bootstrap            # os quatro, na ordem correta
```


Opções úteis:

```bash
# Somente Brasil/regiões/UFs — pula a malha municipal (~60 MB, 27 requisições)
python -m app.jobs.bootstrap --skip-municipal-geometries

# Recorta o período dos indicadores
python -m app.jobs.import_indicators --periods "2022|2023"

# Recorta as UFs nas etapas municipais
python -m app.jobs.import_geometries --states 35,31
```

### Idempotência

Rodar duas vezes com os mesmos dados **não cria linha nova e não reescreve nada**.
Verificado em execução real:

```
1ª execução:  257.603 observações processadas → 509.601 linhas gravadas
2ª execução:    1.518 observações processadas →       0 linhas gravadas
```

Três mecanismos combinados garantem isso:

1. chave natural como PK/UNIQUE em toda tabela de destino
   (`indicator_values` usa `(territory_id, indicator_id, reference_year)`);
2. `ON CONFLICT ... DO UPDATE` em todo upsert, com
   `WHERE valor IS DISTINCT FROM novo valor` — por isso nem o `updated_at` muda;
3. nenhuma coluna sequencial usada como chave de negócio.

### Atualizar os dados

É a mesma coisa que ingerir: rode o job de novo. Valores revisados pela fonte
são atualizados no lugar; anos novos entram como linhas novas.

### Falhas parciais

Antes de desistir de uma requisição, o provider tenta de novo as falhas
transitórias — queda de conexão, timeout, `429` e `5xx` — até 3 vezes, com espera
crescente (2 s, 4 s). Não é hipotético: numa ingestão real, as UFs 42 e 43
caíram com `Server disconnected without sending a response` e responderam na
repetição. Erros definitivos (`404`, corpo que não é JSON) falham na hora.

Se mesmo assim um escopo falhar, os outros não são perdidos: cada escopo (uma UF,
um dataset) commita separadamente. Se 3 de 27 UFs falharem, as outras 24
permanecem gravadas e a execução termina com `status = 'partial'`, com os escopos
falhos listados em `ingestion_runs.details`. Rodar o job de novo completa o que
faltou.

```bash
make psql
select id, job, status, records_processed, records_written, records_failed,
       finished_at - started_at as duracao
  from ingestion_runs order by id desc limit 10;
```

---

## Dados e fontes

### APIs externas consumidas

Todas as fontes são APIs públicas do **IBGE**, consumidas **exclusivamente pelo
backend**, durante a ingestão. O browser nunca as acessa: não há proxy, redirect
nem chamada direta do frontend para o IBGE — o que a aplicação serve vem do
PostgreSQL.

| API | Endpoints consultados | Para quê | Cadastro / chave | Custo |
|---|---|---|---|---|
| [Localidades v1](https://servicodados.ibge.gov.br/api/docs/localidades) | `GET /api/v1/localidades/regioes`<br>`GET /api/v1/localidades/estados`<br>`GET /api/v1/localidades/municipios` | hierarquia territorial: regiões, UFs, municípios, capitais | **não exige** | gratuito |
| [Malhas Geográficas v3](https://servicodados.ibge.gov.br/api/docs/malhas) | `GET /api/v3/malhas/paises/BR` (com `intrarregiao=regiao\|UF`)<br>`GET /api/v3/malhas/estados/{uf}` | geometrias oficiais (GeoJSON) do país, regiões, UFs e municípios | **não exige** | gratuito |
| [Agregados v3 (SIDRA)](https://servicodados.ibge.gov.br/api/docs/agregados?versao=3) | `GET /api/v3/agregados/{tabela}/periodos`<br>`GET /api/v3/agregados/{tabela}/periodos/{p}/variaveis/{v}` | séries de população, área, PIB, renda e desocupação | **não exige** | gratuito |

**Condições de uso.** As três APIs são abertas: não há cadastro, chave, token
nem `Authorization` em nenhuma requisição — a única identificação enviada é um
`User-Agent` (`app/providers/base.py`). A documentação oficial não
publica cota nem limite de requisições; ainda assim a ingestão limita a
concorrência a `IBGE_MAX_CONCURRENCY` (4 por padrão) por cortesia com a fonte.

As estatísticas do IBGE são informação pública produzida por órgão federal, e as
publicações do instituto autorizam a reprodução **desde que citada a fonte** —
por isso cada valor gravado carrega a sua proveniência (tabela, variável e URL)
na tabela `datasets`, e a interface mostra a fonte de cada número. O IBGE não
publica um identificador de licença (SPDX, Creative Commons) para estas APIs;
na dúvida sobre um uso específico, vale consultar os
[canais de atendimento do IBGE](https://www.ibge.gov.br/acesso-informacao/ouvidoria.html).

> O endpoint clássico do SIDRA (`apisidra.ibge.gov.br`) responde **HTTP 403** e
> por isso **não** é usado: os mesmos dados vêm da API de Agregados v3, que é a
> interface programática oficial do SIDRA.

A camada de mapa usa ainda os *tiles* do **Esri World Terrain Base**
(`services.arcgisonline.com/.../World_Terrain_Base`) apenas como fundo visual:
sem cadastro, com a atribuição exigida — "Esri, USGS, NOAA" — exibida no canto
do mapa. Nenhum dado do produto vem de lá; é só contexto cartográfico.

### Indicadores

Tabela, variável e recorte de cada indicador. Tudo verificado contra
`/api/v3/agregados/{tabela}/metadados` na API em produção; o registro executável
está em `app/providers/ibge/datasets.py`.

| Indicador | Unidade | Tabela · variável | Níveis | Cobertura obtida |
|---|---|---|---|---|
| `population` | hab. | 6579 v.9324 (estimativas) · 4714 v.93 (Censo 2022) · 202 v.93 (Censo 2010) | BR/região/UF/município | 2001–2026, 24 anos |
| `population_growth` | % a.a. | **derivado** — variação anual geométrica da população | idem | 2002–2026, 23 anos |
| `area_km2` | km² | 4714 v.6318 (2022) · 1301 v.615 (2010) | idem | 2010, 2022 |
| `population_density` | hab./km² | **derivado** — população ÷ área | idem | 2001–2026, 24 anos |
| `urban_population` | hab. | 9923 v.93 c.1[1] (Censo 2022) · 202 v.93 c.1[1] (Censo 2010) | idem | 2010, 2022 |
| `urbanization_rate` | % | **derivado** — população urbana ÷ população | idem | 2010, 2022 |
| `gdp` | BRL | 5938 v.37 | idem | 2002–2023, 22 anos |
| `gdp_share_national` | % | **derivado** — PIB ÷ PIB do Brasil, no mesmo ano | idem | 2002–2023, 22 anos |
| `gdp_per_capita` | BRL | **derivado** — PIB ÷ população | idem | 2002–2023, 22 anos |
| `gdp_agriculture` | BRL | 5938 v.513 (VAB agropecuária) | idem | 2002–2021, 20 anos |
| `gdp_industry` | BRL | 5938 v.517 (VAB indústria) | idem | 2002–2021, 20 anos |
| `gdp_services` | BRL | 5938 v.6575 + v.525 (VAB serviços + administração pública) | idem | 2002–2021, 20 anos |
| `household_income_per_capita` | BRL | 7395 v.4196 (PNAD Contínua anual) | BR/região/UF | 2016–2025, 9 anos |
| `unemployment_rate` | % | 4562 v.4099 (PNAD anual) · 6468 v.4099 (PNAD trimestral) | BR/região/UF | 2012–2026, 15 anos |

### Periodicidades

O destino é `indicator_values`, com chave `(território, indicador, ano)`. Cada
periodicidade chega até lá pela normalização do provider:

| Periodicidade | Fonte | Tratamento |
|---|---|---|
| **anual** | estimativas, PIB, PNAD anual | um período = um ano, sem transformação |
| **censitária** | Censos 2010 e 2022 | só existem os anos do Censo; nenhuma interpolação é inventada |
| **trimestral** | desocupação (6468) | os trimestres do ano viram a média anual — sem isso o upsert tentaria afetar a mesma linha quatro vezes |

Notas verificadas contra a API em produção:

- O endpoint clássico do SIDRA (`apisidra.ibge.gov.br`) responde **HTTP 403**.
  Os providers usam `/api/v3/agregados`.
- A tabela 5938 **não publica PIB per capita** — daí ele ser derivado.
- A cobertura é irregular: as estimativas não têm 2007 nem 2023 (anos
  censitários e de revisão vêm de outras tabelas); a PNAD anual não tem 2020.
  Por isso `latest` é uma regra de domínio, não açúcar sintático.
- **A abertura setorial do PIB para dois anos antes do PIB total**: a 5938
  publica o total até 2023 e os setores até 2021 (de 2022 em diante os valores
  setoriais vêm como `"..."`). Os anos sem dado simplesmente não nascem.
- **Serviços é a soma de duas variáveis** (6575 privados + 525 administração
  pública). Um teste confere que os três setores fecham o VAB total publicado
  (v. 498) — com só a 6575, não fechariam.
- **População urbana não é variável, é categoria**: sai da mesma variável 93 com
  o recorte `classificacao=1[1]`. Por isso a classificação entra na identidade do
  dataset — senão a população total e a urbana dividiriam a mesma proveniência.
- **Desemprego tem duas fontes de propósito.** Para um ano fechado vale a média
  anual oficial (4562); o ano em curso, que ela ainda não publicou, vem da média
  dos trimestres já divulgados (6468). As duas não coincidem — em 2024, oficial
  6,6% contra 6,85% da média dos trimestres — porque o IBGE calcula a média anual
  sobre a amostra do ano, não sobre as quatro taxas.
- No nível municipal a PNAD Contínua **só apura as capitais** (verificado:
  `N6[N3[35]]` devolve 1 município de 645). Renda e desemprego param em UF, e o
  mapa municipal desses indicadores mostra "sem dado" em vez de pintar 1 de 645.
- **Crescimento populacional em ano censitário carrega a revisão do Censo.** Em
  2022 a taxa compara o Censo (203,1 mi) com a estimativa de 2021 (213,3 mi) e
  sai negativa: é o que a série publicada diz, e está anotado na descrição do
  indicador que aparece no painel.
- O PIB é publicado em "Mil Reais" e normalizado para reais na ingestão.
- Um município (`5101837`, Boa Esperança do Norte/MT) existe na API Localidades
  mas **ainda não está na malha territorial**. O job de geometrias reporta a
  lacuna e o mapa simplesmente não o desenha.

---

## API

Base: `/api/v1`. Erros usam um envelope único, incluindo os 422 de validação:

```json
{ "error": { "code": "territory_not_found", "message": "…", "details": {} } }
```

| Método | Rota | Uso |
|---|---|---|
| `GET` | `/health`, `/health/ready` | liveness e readiness (banco + PostGIS) |
| `GET` | `/contexts` | contextos de dados (sociopolítico, clima/ambiente, biodiversidade) e providers registrados em cada um |
| `GET` | `/indicators?level=&context=` | catálogo + anos com dado |
| `GET` | `/indicators/{key}` | um indicador |
| `GET` | `/territories?level=&parent=&search=&limit=&offset=` | listagens |
| `GET` | `/territories/{ibge_code}` | território, pai, capital, bbox |
| `GET` | `/territories/{ibge_code}/overview?year=` | projeção da tela de detalhe |
| `GET` | `/territories/{ibge_code}/indicators` | séries históricas |
| `GET` | `/map?level=&parent=&indicator=&year=&lod=&classes=` | projeção do mapa |
| `GET` | `/map/values?level=&parent=&indicator=&year=&classes=` | os mesmos valores e classes, sem geometria |
| `GET` | `/views` | visualizações salvas, mais recentes primeiro |
| `POST` | `/views` | cria uma visualização — `201` + `Location` |
| `GET` | `/views/{id}` | uma visualização |
| `PUT` | `/views/{id}` | substitui a visualização inteira — `200` |
| `DELETE` | `/views/{id}` | remove a visualização — `204` |

`lod` aceita `overview` ou `detail`. A geometria canônica fica no banco como
verdade oficial e **não é servida**: a malha municipal crua de Minas Gerais tem
8,8 MB, e expô-la em um parâmetro anularia a estratégia de carregamento
progressivo.

O código IBGE é o identificador público; IDs internos do banco não aparecem em
nenhum contrato.

### O endpoint do mapa

Uma requisição devolve tudo que o mapa precisa — o cliente não busca indicador e
geometria separadamente para depois juntar:

```bash
curl "http://localhost:8000/api/v1/map?level=state&indicator=gdp_per_capita&year=latest"
```

```json
{
  "type": "FeatureCollection",
  "scope":     { "level": "state", "parent": null, "lod": "overview", "count": 27 },
  "indicator": { "key": "gdp_per_capita", "name": "PIB per capita", "unit": "BRL",
                 "year": 2023, "requestedYear": "latest", "availableYears": [2002, "…"] },
  "statistics":{ "min": 22020.63, "max": 129790.43, "median": 41047.91,
                 "count": 27, "missing": 0 },
  "classification": { "method": "quantile", "classes": 5, "breaks": ["…"] },
  "bbox": [-73.99, -33.75, -29.3, 5.27],
  "features": [ { "type": "Feature", "id": "35", "properties": { "…": "…" },
                  "geometry": { "type": "MultiPolygon", "coordinates": [] } } ]
}
```

É um **GeoJSON válido** com membros estrangeiros, então o componente `<GeoJSON>`
do react-leaflet consome a resposta sem transformação alguma.

Drill-down carrega apenas os municípios da UF pedida:

```bash
curl "http://localhost:8000/api/v1/map?level=municipality&parent=35&indicator=population"
```

`level=municipality` **sem** `parent` é rejeitado com HTTP 400: a proteção contra
transferir a malha municipal do país inteiro é aplicada pela API, não confiada ao
cliente.

### Visualizações salvas (o CRUD do usuário)

As oito primeiras rotas são projeções de leitura sobre o que a ingestão trouxe
do IBGE. As cinco últimas são a única família que **escreve**: um recorte do
mapa que o usuário quis guardar — nível, pai, indicador, ano e número de classes
— com um nome.

É o mesmo objeto que os filtros da interface já produzem, então não há entidade
inventada só para ter escrita: salvar é gravar o que está na tela, e abrir é
repor os filtros de uma vez.

```bash
# criar
curl -X POST http://localhost:8000/api/v1/views \
  -H 'Content-Type: application/json' \
  -d '{"name":"PIB per capita por estado","level":"state",
       "indicatorKey":"gdp_per_capita","year":"latest","classes":5}'
```

```json
{
  "id": "21065c91-257a-4921-b661-239e88e75ef9",
  "name": "PIB per capita por estado",
  "description": null,
  "level": "state",
  "parentCode": null,
  "indicatorKey": "gdp_per_capita",
  "year": "latest",
  "classes": 5,
  "createdAt": "2026-09-16T13:56:43.735211Z",
  "updatedAt": "2026-09-16T13:56:43.735211Z"
}
```

```bash
curl http://localhost:8000/api/v1/views                    # listar
curl -X PUT    http://localhost:8000/api/v1/views/{id} ...  # substituir
curl -X DELETE http://localhost:8000/api/v1/views/{id}      # remover (204)
```

Na interface, a lista fica em uma seção do painel: clicar numa linha repõe
indicador, ano e recorte de uma vez; o lápis renomeia (`PUT`) e a lixeira pede
confirmação antes do `DELETE` — exclusão não tem desfazer, e o segundo clique é
o que separa um engano de uma decisão.

O `id` é um **UUID público**: como no resto da API, o id serial do banco não
aparece em nenhum contrato. `year` aceita `"latest"` ou um ano — exatamente como
a rota `/map`, para o cliente guardar e reenviar o mesmo valor que já usa no
seletor de ano. Internamente `"latest"` é `NULL` na coluna `reference_year`.

Uma visualização só é gravada se puder ser aberta: as mesmas validações da rota
`/map` são aplicadas **na escrita**.

| Situação | Resposta |
|---|---|
| Indicador fora do catálogo | `404 indicator_not_found` |
| `level=municipality` sem `parentCode` | `400 invalid_parameter` |
| `parentCode` inexistente | `404 territory_not_found` |
| `parentCode` de nível errado (uma região como pai de município) | `400 invalid_parameter` |
| Nome já usado (ignorando caixa) | `409 saved_view_name_taken` |
| Ano fora de 1900–2100, nome em branco, UUID inválido | `422 validation_error` |
| `GET`/`PUT`/`DELETE` de um id inexistente | `404 saved_view_not_found` |

A regra "município exige pai" é dupla: o serviço devolve `400` com mensagem, e um
`CHECK` na tabela impede que uma visualização impossível de abrir seja gravada
por qualquer outro caminho.

As rotas de escrita usam a dependência `get_write_session`, que abre uma
transação por requisição e faz rollback em qualquer exceção. As de leitura usam
`get_session`, que nunca commita — uma escrita acidental num caminho `GET` não
passa despercebida.

Para verificar o ciclo inteiro contra a API no ar:

```bash
make smoke
```

```
✓ GET  /health/ready            → 200
✓ GET  /map?level=state         → 200 (27 territórios)
✓ POST /views                   → 201
✓ GET  /views                   → 200
✓ GET  /views/{id}              → 200
✓ PUT  /views/{id}              → 200 (recorte substituído)
✓ DELETE /views/{id}            → 204
✓ GET  /views/{id} após DELETE  → 404 (removida do banco)
```

### Política de ausência de dados

| Situação | Resposta |
|---|---|
| Indicador sem valor para o território | `200`, `value: null`, `year: null` |
| Ano pedido sem dado | `200`, valores nulos; a geometria continua presente |
| Ano escolhido não existe no nível aberto | cai para "último disponível" sem descartar a escolha quando ela é válida |
| `year=latest` sem dado algum | `200`, `indicator.year: null`, `statistics: null` |
| Código IBGE inexistente | `404 territory_not_found` |
| Indicador inexistente | `404 indicator_not_found` |
| `level=municipality` sem `parent` | `400 invalid_parameter` |
| `parent` em `level=country` (a raiz não tem pai) | `400 invalid_parameter` |
| `lod=canonical` | `422 validation_error` |
| Parâmetro com tipo inválido | `422 validation_error` |

Território sem dado **aparece** no mapa com `value: null` — o país precisa ser
desenhado por inteiro, e é o frontend que estiliza "sem dado".

---

## Performance

Medições reais nesta máquina, com os dados do IBGE carregados:

| Endpoint | Sem gzip | Com gzip | Latência |
|---|---|---|---|
| `/map?level=state&indicator=…` | 136 KB | **45 KB** | 16 ms |
| `/map?level=municipality&parent=35` (645) | 1,1 MB | 300 KB | ~135 ms |
| `/map?level=municipality&parent=31` (853) | 2,0 MB | 556 KB | ~256 ms |
| `/territories/35/overview` | 1,3 KB | — | 5 ms |
| `/indicators?level=state` | 2,1 KB | — | 9 ms (1 ms em cache) |

O cache em processo guarda apenas projeções pequenas (até
`READ_CACHE_MAX_FEATURES` feições). Uma coleção municipal desserializada ocupa
~7 MB: cachear 40 delas levava a API de 92 MB para 378 MB de RSS, com pouco
acerto porque cada usuário abre um estado diferente. As projeções realmente
compartilhadas — a visão inicial do país — continuam cacheadas.

O que sustenta esses números:

1. **Simplificação na ingestão, nunca por requisição.** A malha municipal de
   Minas Gerais tem 8,8 MB e 401.746 vértices na qualidade máxima. Os LODs
   pré-computados reduzem os municípios de 2.583.201 para 647.097 vértices
   (`detail`) e 85.798 (`overview`).
2. **Carregamento progressivo.** A aplicação abre com 27 estados; municípios só
   são buscados ao entrar em um estado, e somente os daquele estado.
3. **Índices ditados pelas consultas reais**, incluindo
   `(indicator_id, reference_year) INCLUDE (territory_id, value)`, que torna a
   leitura do mapa index-only.
4. **GZip + ORJSON**, porque o custo dominante do mapa é transferir bytes.
5. **Cache TTL em processo** para projeções estáticas e Redis compartilhado para clima e camadas para as projeções de leitura,
   que mudam apenas quando a ingestão roda.

Não há materialized view: o endpoint do mapa lê 27 (ou ≤853) linhas por índice e
o gargalo é rede, não CPU. O raciocínio e o gatilho para reavaliar isso estão em
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) §8.

---

## Qualidade

```bash
make check   # testes + lint
make test    # 91 testes
make lint    # ruff + ruff format + mypy
make smoke   # GET/POST/PUT/DELETE contra a API no ar
```

Sem `make`:

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml run --rm api sh -c "alembic upgrade head && pytest -q"
./scripts/smoke_crud.sh
```

O lint do frontend (`eslint`, `tsc`, `prettier`) fica no repositório dele.

Os testes se dividem em dois grupos:

- **unitários** — normalização dos providers, classificação, fórmula dos
  derivados e o contrato das visualizações (a tradução `latest` ↔ `NULL` ↔ ano).
  Não usam rede: as fixtures em `tests/fixtures/` são recortes de
  respostas reais da API do IBGE.
- **integração** (`@pytest.mark.db`) — idempotência da ingestão (exercitando o
  SQL de upsert real), as consultas do mapa e o ciclo POST → GET → PUT → DELETE
  das visualizações. Pulam com mensagem clara se o banco não estiver disponível,
  e rodam dentro de transações revertidas.

Alguns testes merecem destaque por exercitarem regras que erram silenciosamente.
Os quatro primeiros comparam o que derivamos com números que o **próprio IBGE
publica** — um erro de fórmula, unidade ou multiplicador falha contra uma
referência oficial, não contra um valor que inventamos:

- densidade × densidade publicada (tabela 4714, v. 614);
- taxa de urbanização × percentual publicado (tabela 9923, v. 1000093 — 87,41%
  no Brasil em 2022);
- participação no PIB × participação publicada (tabela 5938, v. 496);
- crescimento anualizado × a taxa intercensitária divulgada pelo IBGE
  (0,52% a.a. entre os Censos de 2010 e 2022);
- a soma dos três setores × o VAB total publicado (tabela 5938, v. 498);
- a colisão de identificadores entre níveis territoriais (no nível N1 o Brasil
  vem com `id = "1"`, o mesmo id da região Norte em N2) tem teste próprio, porque
  um mapeamento ingênuo atribuiria a população do Brasil à região Norte;
- a série trimestral não pode produzir duas observações para o mesmo ano: seria
  um erro de upsert no PostgreSQL, que só apareceria contra o banco;
- uma visualização só é gravada se puder ser aberta: indicador fora do catálogo,
  município sem pai e pai de nível errado têm teste cada um, porque são erros
  que só apareceriam quando o usuário tentasse abrir o que salvou.

---

## Como estender

### Adicionar um indicador de fonte já suportada

Sem código novo de infraestrutura:

1. Descreva a origem em `app/providers/ibge/datasets.py`
   (tabela, variável, níveis, multiplicador de unidade, períodos).
2. Adicione o indicador ao catálogo em `app/jobs/seed_indicators.py`.
3. `make seed && docker compose run --rm api python -m app.jobs.import_indicators`

Isso foi validado na prática duas vezes: as fontes de 2010 (tabelas 202 e 1301) e
depois o PIB por setor, a população urbana e a renda domiciliar entraram como
entradas no registro, sem tocar em modelo, API ou frontend. O indicador aparece
no seletor assim que a ingestão roda — o catálogo é lido da API, não codificado
na interface.

Uma fonte com característica nova pode pedir um campo no registro; os que existem
hoje cobrem multiplicador de unidade, períodos, níveis e recorte de classificação
(`classificacao=1[1]`, que é como se obtém a população urbana).

### Adicionar um indicador derivado

Uma entrada em `DERIVED_INDICATORS`, escolhendo a forma de derivação:

| Forma | Declara | Exemplo |
|---|---|---|
| `RatioIndicatorSpec` | numerador, denominador, fator | densidade, PIB per capita, urbanização |
| `GrowthIndicatorSpec` | indicador base | crescimento populacional |
| `ShareIndicatorSpec` | indicador base | participação no PIB nacional |

As regras temporais de cada forma já são compartilhadas: a razão busca o
denominador de referência mais recente até o ano; o crescimento compara com o ano
anterior *com dado* e anualiza o intervalo; a participação exige o total nacional
do mesmo ano.

### Adicionar uma fonte nova (SICONFI, IPEA, DataSUS, INEP, INMET, GBIF...)

Um módulo em `app/providers/<fonte>/` que devolva `IndicatorObservation`
(de `app.providers.records` — a fronteira, compartilhada por qualquer fonte),
um `dataset` novo, um `PROVIDER: ProviderDescriptor` registrado em
`app/providers/registry.py` (contexto + indicadores que a fonte fornece) e um
job (ou um parâmetro no job existente). **Modelo, schema e API não mudam** —
é para isso que `IndicatorObservation` existe como fronteira. Detalhe da
receita, com os dois acoplamentos que foram corrigidos para viabilizá-la, em
[docs/ARCHITECTURE.md §10-11](docs/ARCHITECTURE.md#10-contextos-de-dados-e-registro-de-providers).

### Contextos de dados

Cada indicador pertence a um `DataContext` (`sociopolitical`,
`climate_environmental` ou `biodiversity` — ver `app/models/context.py`).
`GET /contexts` lista os contextos e os providers registrados em cada um;
`GET /indicators?context=` filtra o catálogo por contexto. Os dois novos
contextos já existem e já respondem em `/contexts`, sem nenhum provider
registrado ainda — prontos para receber o primeiro sem migration.

### Adicionar um nível territorial

Um valor novo no enum `territory_level`, as duas linhas de hierarquia ao lado dele
(`REQUIRES_PARENT` e `EXPECTED_PARENT_LEVEL`, em `app/models/territory.py`)
e a ingestão correspondente. Modelo, mapa, visualizações salvas e API já são
genéricos por nível: quem valida um recorte — `/map` e `/views` — lê a hierarquia
desse mesmo lugar, e não de uma cópia própria.

---

## Estrutura e convenções

```
docker-compose.yml        db + api + web — a aplicação completa
docker-compose.dev.yml    sobreposição com recarga automática da API
Dockerfile                imagem da API
Makefile                  atalhos opcionais do ciclo de desenvolvimento
.env.example              configuração opcional (tudo tem default)
pyproject.toml            dependências, ruff, mypy, pytest
alembic.ini
alembic/versions/         migrations
app/
  api/v1/                 rotas HTTP, validação de entrada, cabeçalhos de cache
  core/                   configuração, logging, erros, cache
  db/                     engine, sessão, base declarativa
  models/                 SQLAlchemy 2.0 (hierarquia territorial, contextos de dados)
  schemas/                Pydantic v2 (contratos da API)
  repositories/           SQL e PostGIS
  services/               latest, classificação, derivação, overview, visualizações
  providers/              records.py (fronteira comum), specs.py (derivação),
                          registry.py (providers por contexto), ibge/ (fonte IBGE)
  jobs/                   orquestração da ingestão
tests/                    unitários + integração (fixtures de respostas reais do IBGE)
scripts/smoke_crud.sh     verificação GET/POST/PUT/DELETE de ponta a ponta
docs/ARCHITECTURE.md      decisões arquiteturais e alternativas recusadas
```

A estrutura do frontend está no [README dele](https://github.com/henriquexaud/brasil-lens-frontend#estrutura).

### Convenções

| Camada | Convenção | Exemplos |
|---|---|---|
| Módulos, funções e variáveis Python | `snake_case` | `saved_views.py`, `list_territories()`, `parent_code` |
| Classes Python | `PascalCase` | `SavedView`, `TerritoryLevel`, `DomainError` |
| Tabelas e colunas | `snake_case`, tabela no plural | `saved_views`, `indicator_values.reference_year` |
| Contrato HTTP | `camelCase` (traduzido pelo Pydantic) | `parentCode`, `indicatorKey`, `availableYears` |
| Componentes React (frontend) | arquivo e símbolo em `PascalCase` | `ControlPanel.tsx`, `SavedViewsPanel.tsx` |

O `ruff` (linter + formatador) e o `mypy` em modo estrito governam este
repositório; no frontend, `eslint`, `prettier` e `tsc --noEmit`. Nenhum dos dois
tem `any` nem `# type: ignore` sem justificativa no lugar.

---

## Escopo desta fundação

**Incluído:** hierarquia territorial completa, 14 indicadores com histórico
(9 de fonte + 5 derivados), geometrias em três níveis de detalhe, mapa
coroplético com seleção de indicador e ano, hover com prefetch, painel de
detalhe, drill-down por estado, visualizações salvas (CRUD completo), ingestão
idempotente com registro de execuções, migrations, testes e lint.

**Deliberadamente fora:** vector tiles, comparação entre territórios, gráficos
históricos, autenticação, filas, GraphQL, microserviços. A estrutura para
séries históricas existe (`/territories/{code}/indicators` já devolve as séries);
os gráficos, não.

As visualizações salvas são **globais**, não por usuário: sem autenticação no
escopo, um `owner_id` seria um campo sem dono de verdade. O dia em que houver
login, a coluna entra por migration e o serviço filtra por ela — o modelo não
precisa mudar de forma.

### Clima atual e previsão

`GET /api/v1/weather/current` consulta as 27 capitais na
[Open-Meteo](https://open-meteo.com/en/docs), sem chave. O parâmetro opcional
`?territory=3509502` consulta um município pelo código IBGE; para UFs, como
`?territory=35`, retorna a capital como referência, nunca uma média estadual.
A consulta municipal usa um ponto interno da geometria IBGE no PostGIS.

`forecast=false` solicita apenas condições atuais; o padrão `true` mantém
a previsão de três dias para clientes existentes. O frontend usa a consulta
completa somente quando a seção de previsão é expandida.

`GET /api/v1/weather/municipalities?parent=35&offset=0&limit=40` retorna as
condições atuais dos municípios da UF em ordem de código IBGE. `nextOffset`
indica o próximo lote, ou `null` ao terminar. O limite máximo é 40; a consulta
usa uma chamada com múltiplas coordenadas e aquece o cache de cada município.

Retorna temperatura, sensação térmica, umidade, vento em km/h, precipitação
com duração explícita do intervalo e previsão diária de três dias. Toda leitura
traz também a chuva acumulada nas últimas 24 h (`precipitation24hMm`, a soma
horária até o instante da leitura), o total e a probabilidade de chuva de hoje e
`rainingNow` (precipitação no último intervalo ou código WMO de chuva) — na mesma
chamada, sem passar de dez variáveis por coordenada. São
estimativas de modelos, não medições de estações. Os instantes são UTC e as
datas da previsão respeitam o fuso do local.

O cache guarda uma leitura por município, compartilhada por todas as rotas e
usuários (memória e Redis): a cidade selecionada vale 15 minutos a partir do
horário da leitura, as camadas do mapa 30 minutos; depois disso a leitura é
servida enquanto é renovada em segundo plano, por até duas horas. Com a fonte
fora do ar ou sem cota, o último resultado de até doze horas é servido como
`stale`, com os horários originais; sem dado utilizável a API responde com o
erro real. Uma queda pausa a fonte por um minuto; a cota esgotada, pelo tempo
indicado pela Open-Meteo. Uma consulta selecionada nunca espera um lote de fundo.

`/weather/alerts` agrega duas fontes complementares, atualizadas pelo mesmo
scheduler: o **INMET** avisa sobre o fenômeno meteorológico (chuva intensa,
tempestade, vento, baixa umidade, onda de calor); o **CEMADEN** avisa sobre o
risco/impacto geo-hidrológico associado (inundação, enxurrada, alagamento,
deslizamento). Os dois produzem o mesmo modelo normalizado — `category`
(`meteorological`/`geo_hydrological`) e `severityLevel` (`potential`/
`danger`/`extreme`/`other`) são calculados a partir do vocabulário de cada
fonte em `services/weather.py`, então o frontend nunca precisa saber o
formato de nenhuma delas para decidir cor ou proeminência. Um aviso de chuva
do INMET e um risco de deslizamento do CEMADEN na mesma cidade continuam dois
alertas distintos — nunca são fundidos em um só. `provider` identifica a
fonte em cada alerta (`"inmet"` ou `"cemaden"`).

Assim como o INMET (`apiprevmet3.inmet.gov.br`, sem contrato de API
publicado), o CEMADEN também não documenta uma API pública formalmente; a
fonte usada (`app/providers/cemaden/`) é o GeoServer WFS que alimenta o
próprio [Mapa Interativo](https://mapainterativo.cemaden.gov.br) do CEMADEN
(camada `alertas_vigentes_siaden`) — protocolo OGC padrão e autodescritivo,
a mesma técnica já usada para o INPE/BDQueimadas, o que reduz o risco de
quebra mesmo sem documentação de negócio. Uma diferença real fica isolada no
provider: o CEMADEN não manda uma data de expiração explícita, só um carimbo
de "última confirmação" (`vigencia`) reemitido enquanto o risco persiste — a
expiração gravada é `vigencia` mais um buffer configurável
(`CEMADEN_ALERT_VALIDITY_BUFFER_SECONDS`, padrão 4 h), para um ciclo do
scheduler perdido não apagar um risco real do mapa.

A importação legada de estações não roda no scheduler, pois não estava
produzindo leituras utilizáveis. `/weather/stations` continua compatível com
dados previamente ingeridos. `/weather/sources` informa o estado da ingestão
de cada fonte de alerta; `/weather/current` inclui sua própria fonte, status e
horário de consulta. A API gratuita da Open-Meteo se destina a uso não
comercial; consulte os termos do provedor para publicação comercial.

### Focos de calor (INPE)

A camada usa os serviços públicos do [BDQueimadas](https://data.inpe.br/queimadas/bdqueimadas/),
sem chave ou cadastro. O frontend atualiza a consulta a cada dez minutos enquanto
ela estiver ativa em **Clima e meio ambiente → Camadas e fontes**. A atualização
acompanha a publicação do INPE; uma detecção por satélite não confirma que o fogo
continua ativo e várias detecções podem corresponder ao mesmo evento.

- `GET /api/v1/fire-hotspots?level=country&hours=48`: contagem completa, horários,
  configuração WMS e uma prévia com a detecção mais recente. `features` **não** é
  o conjunto completo. O mapa aberto usa agregação completa; o WMS desenha os
  pontos reais apenas no zoom próximo, sem um limite artificial de detecções.
- `level=state&parent=15` ou `level=municipality&parent=1504604`: filtro pelos
  códigos IBGE nativos do INPE (`id_1`/`id_2`), sempre restrito ao Brasil (`id_0=33`).
  Um território inexistente retorna 404; recortes incompatíveis retornam 400.
- `GET /api/v1/fire-hotspots/identify`: recebe `latitude`, `longitude`, `tolerance`
  em graus (até 0,5), `at` igual ao `metadata.windowEnd` e os mesmos filtros.
  Retorna até 20 detecções recentes próximas ao clique e a contagem total da área;
  os resultados são ordenados por proximidade. Não carrega detalhes ao abrir o mapa.

O WFS oficial é `https://data.inpe.br/queimadas/geoserver/wfs`, camada
`bdqueimadas:focos`. O WMS usa o mesmo caminho com `/wms`. As URLs e o timeout
podem ser alterados por `INPE_QUEIMADAS_WFS_URL`, `INPE_QUEIMADAS_WMS_URL` e
`INPE_QUEIMADAS_HTTP_TIMEOUT`. TLS permanece verificado. Não há dependência NASA FIRMS.

O cache de metadados dura dez minutos e deduplica consultas simultâneas. Falha
da fonte retorna 502, nunca um falso resultado vazio. Uma consulta anterior de
até uma hora pode ser devolvida com `status=stale`, preservando período e horários.
O mapa usa o mesmo período da contagem e da consulta pontual. Ausências e sentinelas
negativas são normalizadas para `null`; risco de fogo permanece um índice, não uma
porcentagem. FRP é potência radiativa em MW, não área queimada.

- `GET /api/v1/fire-hotspots/summary`: mesmos filtros e `at=metadata.windowEnd`.
  Lê o WFS em CSV paginado (páginas de 10 mil, até quatro ao mesmo tempo; uma
  falha cancela as que esperam a vez), valida contagem e IDs únicos e devolve
  estatísticas de estados e municípios. `density = count × 1000 / areaKm2`.
  As áreas, a consulta mais lenta (~2 s), são aquecidas em segundo plano ao
  subir a API.
  A área de cada estado/município é geodésica, calculada no PostGIS sobre sua malha **canônica** IBGE (não
  sobre o LOD simplificado); `areaSource` explicita essa metodologia. Área ausente
  gera densidade nula. Contagens de 24h e do período, última detecção e registros
  sem município reconhecido são preservados. Falha de paginação retorna 502.
- `GET /api/v1/fire-hotspots/municipalities?bbox=west,south,east,north`: apenas
  limites municipais canônicos paginados (`offset`, `limit`, `nextOffset`), sem focos individuais.
- `GET /api/v1/hydrography?zoom=4&bbox=...&include_water_bodies=false`: rios por
  área de drenagem e simplificação por escala. `include_rivers=false` permite buscar
  polígonos de lagos separadamente, depois dos rios. A ANA é consultada por IDs em
  páginas completas; falha transitória devolve `status=partial`, sem cache de 24h.

Os coropléticos estadual e municipal usam a mesma escala quantitativa de focos
por 1.000 km²; não representam extensão queimada. Não há comparação
com período anterior sem uma segunda janela validada.


### Municípios visíveis, localização e Redis

- `POST /api/v1/territories/locate`, corpo `{ "latitude": -23.55, "longitude": -46.63 }`:
  município que contém o ponto (`ST_Covers` da malha canônica), pai e bbox. Consulta
  local, sem geocoder externo, sem cache e sem coordenadas na URL de access logs.
- `GET /api/v1/weather/municipal-boundaries?bbox=...&offset=0&limit=24`: malhas
  **canônicas** visíveis, incluindo vizinhos de outras UFs. Alternativamente,
  `parent=35` pagina a UF (capital primeiro) e `code=3550308` busca a seleção.
  `nextOffset=null` indica fim; limite máximo de 40 geometrias por resposta.
  A consulta preserva as coordenadas da ingestão, sem simplificar nem recortar
  os polígonos. A chave de cache distingue recorte, código e página.
- `GET /api/v1/weather/current?forecast=false`: as 27 capitais numa consulta, a
  primeira etapa do mapa do Brasil. Selecionar uma UF consulta apenas sua
  capital e reutiliza a mesma leitura.
- `GET /api/v1/weather/states`: a segunda etapa — clima e chuva de cada UF como a
  média de pontos espalhados pelo território, um a cada ~60 mil km² (de 2 a 8,
  109 no país). São os primeiros da amostra de dispersão do estado (a capital e
  os mais afastados dela), reaproveitados ao abrir a UF; as capitais já lidas
  não voltam à fonte. Cada ponto pesa a área que representa: cada município soma
  a sua ao ponto medido mais próximo (polígonos de Thiessen sobre a malha
  canônica). Temperatura, umidade, vento e chuva são médias; o céu é o que cobre
  a maior área; chance de chuva e `rainingNow` valem se valem para algum ponto.
  `samplePoints` e `rainingPoints` dizem quantos pontos compõem a média e
  quantos têm chuva agora. Sem as leituras novas, a UF volta a ser a capital,
  marcada como dado anterior. Amostra e pesos dependem só da malha e são
  calculados uma vez por processo.
- `GET /api/v1/weather/viewport?bbox=...&zoom=8&parent=35`: clima atual de todos os
  municípios que intersectam a área, do centro para as bordas. Mede um município por
  célula da grade (0,5° no zoom 8, 0,25° no 9, todos a partir do 10) e estima os demais;
  reaproveita a amostra do estado e qualquer leitura já guardada.

O Compose inicia Redis na rede interna, sem porta pública, com volume AOF, limite
256 MB e política allkeys-lru. `REDIS_URL` configura o serviço; vazio mantém fallback
local. Chaves versionadas e comprimidas guardam as leituras de clima por município
(até 12h, com a validade decidida pelo horário da leitura), metadados INPE (10 min),
resumo de intervalo (2h), projeções do mapa (24h, por versão da ingestão) e
malhas/hidrografia (24h).
Falha ou timeout de Redis não bloqueia a fonte; TTLs e idade de observação continuam
validados. Geolocalização precisa não é persistida nesse cache.
