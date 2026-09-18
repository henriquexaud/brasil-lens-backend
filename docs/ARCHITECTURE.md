# Brasil Lens — Arquitetura da Fundação

Documento de decisões. Descreve **o que foi escolhido e por quê**, incluindo o que foi
deliberadamente **não** construído.

---

## 1. Avaliação crítica da proposta

### 1.1 Decisões boas (mantidas sem alteração)

- **Frontend nunca fala com IBGE/SIDRA.** É a decisão mais importante do documento
  original e está correta por três motivos independentes: latência, disponibilidade e
  formato. As APIs do IBGE respondem em centenas de ms a segundos, mudam de contrato sem
  aviso (comprovado na seção 1.3) e retornam estruturas que exigem junção no cliente.
- **Monólito modular.** O produto tem um único domínio coeso (território + indicador +
  tempo + geometria). Não existe fronteira de serviço a extrair.
- **Endpoints orientados ao produto**, não à fonte. `/ibge/...` vazaria a fonte no
  contrato público e travaria a troca de provedor.
- **Projeção de leitura para o mapa.** Um endpoint que entrega geometria + valor +
  estatística em uma resposta é o desenho certo: elimina N+1 no cliente e permite
  otimizar a query real em vez de otimizar peças isoladas.
- **`normalizedValue` não persistido.** Correto — a normalização depende do conjunto
  consultado, não do território.
- **Códigos IBGE como identificadores canônicos na API.** Torna o contrato estável e
  independente de sequences internas.

### 1.2 Pontos que alterei

| Proposta original | Decisão | Motivo |
|---|---|---|
| `territories.area_km2` como coluna | Área é **indicador** (`area_km2`) | A área territorial do IBGE tem **ano de referência** e é revisada. Como coluna, ela seria o único "indicador" com caminho de leitura próprio, forçando um `if` especial no endpoint do mapa. Como indicador, tudo usa um único caminho. |
| `indicators.source` | Proveniência na tabela `datasets`, referenciada pelo **valor** | O mesmo indicador vem de datasets diferentes por período/nível (população: Censo 2022 vs. estimativas 2024). Fonte é atributo do valor, não do indicador. |
| `/regions`, `/states`, `/states/{c}`, `/states/{c}/municipalities`, `/municipalities/{c}` | **`/territories`** + `/territories/{ibge_code}` | São a mesma query com filtro diferente sobre a mesma tabela. 5 endpoints → 2, sem perda de capacidade. |
| `/map/states` + `/map/states/{c}/municipalities` | **`/map`** com `level` + `parent` | Idem: é uma query com escopo diferente. Um endpoint = um plano de execução para otimizar, uma chave de cache, um contrato. |
| Contrato de mapa com `features: []` customizado | **GeoJSON FeatureCollection** com membros estrangeiros (`indicator`, `statistics`, `classification`) | GeoJSON permite membros extras. O `<GeoJSON>` do react-leaflet consome a resposta **sem transformação nenhuma** — que é justamente o objetivo declarado. |
| `min`/`max` apenas | `statistics` + `classification.breaks` (quantis) | PIB per capita é fortemente assimétrico: normalização linear pinta 90% do mapa com a mesma cor. Quebras por quantil são o default correto. A API decide **valores e intervalos**; o frontend decide **cores**. |

### 1.3 Riscos reais identificados (com evidência coletada)

Validei as fontes antes de escrever o provider. Resultados:

1. **`apisidra.ibge.gov.br` retorna HTTP 403.** O endpoint clássico do SIDRA está atrás
   de proteção anti-bot. → O provider usa **`/api/v3/agregados`**, que responde 200, tem
   JSON tipado e permite recorte territorial hierárquico.
2. **O parâmetro `qualidade` das malhas mudou de numérico para textual.**
   `qualidade=4` → HTTP 400 `"aceita apenas UM dos seguintes valores: minima,
   intermediaria ou maxima"`. Contratos externos quebram; por isso a conversão está
   isolada no provider.
3. **A malha municipal de MG tem 8,8 MB e 401.746 vértices** (medido). Enviar isso ao
   browser é inviável. Simplificação em ingestão não é otimização prematura: é requisito.
4. **A tabela 5938 (PIB) não possui variável "per capita"** (46 variáveis verificadas,
   nenhuma de per capita). → `gdp_per_capita` **precisa** ser derivado. Isso confirma a
   necessidade da camada de indicadores derivados.
5. **Anos disponíveis são irregulares.** A tabela 6579 (estimativas) publica
   `2001..2006, 2008, 2009, 2011..2021, 2024..2026` — sem 2007, 2010, 2022, 2023
   (anos censitários vêm de outras tabelas). Isso torna `latest` uma regra de domínio
   obrigatória, não um açúcar sintático.
6. **Nomes divergem entre fontes.** No nível N6 o SIDRA retorna `"Adamantina - SP"`;
   Localidades retorna `"Adamantina"`. → Nomes vêm **exclusivamente** de Localidades;
   SIDRA é usado apenas para valores, casados por código IBGE.

### 1.4 Complexidade que recusei

- Vector tiles / viewport / bbox: com LOD pré-computado, 27 estados cabem em ~40 KB
  gzip e 853 municípios em ~600 KB. Tiles resolveriam um problema que ainda não existe.
- Materialized views: ver §8.
- Redis, filas, autenticação, CQRS, `use_cases/`, `gateways/`: fora do escopo e sem
  problema concreto a resolver.
- Tabela de agregação hierárquica: o IBGE já publica os níveis N1 (Brasil), N2 (região),
  N3 (UF) e N6 (município). Não há necessidade de somar filhos.

---

## 2. Arquitetura final

```
        ┌── INGESTÃO (offline, por CLI) ───────────────────────────────┐
        │                                                             │
 IBGE   │  providers/ibge/*        jobs/*              PostGIS        │
 HTTP ──┼─> localidades.py  ──┐                                       │
        │   malhas.py       ──┼─> normalização ─> upsert ─> territories│
        │   agregados.py    ──┘   (tipos internos)         geometries  │
        │                                                  ind_values  │
        │                         services/derived.py ────> derivados  │
        └─────────────────────────────────────────────────────────────┘
                                          │
        ┌── LEITURA (online, por requisição) ──────────────────────────┐
        │  repositories/  ─>  services/  ─>  api/v1/  ─>  React        │
        │  (SQL/PostGIS)      (regras)       (HTTP)       (Leaflet)    │
        └─────────────────────────────────────────────────────────────┘
```

O ponto central: **as duas metades são separadas no tempo**. Nenhuma requisição de
usuário atravessa a fronteira HTTP do IBGE. O trabalho caro (download, simplificação
geométrica, derivação) acontece na ingestão.

### Responsabilidades

| Camada | Responsabilidade | O que NÃO faz |
|---|---|---|
| `providers/` | Falar HTTP com a fonte externa e converter a resposta em **dataclasses internas** (`TerritoryRecord`, `IndicatorObservation`, `TerritoryGeometry`). | Não conhece SQLAlchemy nem o banco. |
| `jobs/` | Orquestrar ingestão: chamar provider → validar → upsert → registrar `ingestion_run`. Transação por lote. | Não contém regra de negócio de leitura. |
| `repositories/` | SQL e PostGIS. Retorna linhas/dataclasses. | Não decide política (ex.: o que fazer quando não há dado). |
| `services/` | Regras da aplicação: resolver `latest`, classificar, normalizar, montar overview, derivar indicadores. | Não conhece HTTP nem Pydantic de request. |
| `api/` | Rotas, validação de entrada, serialização, cabeçalhos de cache, mapeamento de erro → HTTP. | Não contém regra de domínio. |

Não existem `interfaces/`, `use_cases/`, `factories/` ou `handlers/`: cada camada acima
resolve um problema verificável.

---

## 3. Estrutura dos repositórios

O projeto é entregue em **dois repositórios independentes**. O backend é o
principal: além da API, ele carrega o `docker-compose.yml` que sobe a aplicação
inteira — o serviço `web` é construído direto do repositório do frontend (o
Compose aceita uma URL git como contexto de build), então basta clonar um
repositório para rodar tudo.

```
brasil-lens-backend/              ← projeto principal
├── docker-compose.yml            # db (PostGIS) + api + web (frontend via URL git)
├── docker-compose.dev.yml        # api com uvicorn --reload
├── Dockerfile
├── Makefile                      # atalhos opcionais: up, ingest, test, lint, smoke
├── .env.example
├── pyproject.toml
├── alembic.ini
├── alembic/versions/             # 0001_initial_schema, 0002_saved_views
├── app/
│   ├── main.py                   # app FastAPI, middlewares, handlers de erro
│   ├── core/
│   │   ├── config.py             # Settings (pydantic-settings)
│   │   ├── logging.py            # logging estruturado
│   │   ├── errors.py             # exceções de domínio + envelope de erro
│   │   └── cache.py              # cache TTL em processo (sem Redis)
│   ├── db/{base,session}.py
│   ├── models/                   # SQLAlchemy 2.0 (Mapped/mapped_column)
│   ├── schemas/                  # Pydantic v2 (contratos da API)
│   ├── repositories/             # queries; map_projection.py é a query do mapa
│   ├── services/                 # territories, indicators, map, saved_views, ...
│   ├── providers/ibge/           # localidades, malhas, agregados, datasets, reference
│   ├── api/v1/                   # health, territories, indicators, map, saved_views
│   └── jobs/                     # seed_indicators, import_territories,
│                                 # import_geometries, import_indicators, bootstrap
├── tests/
├── scripts/smoke_crud.sh         # GET/POST/PUT/DELETE de ponta a ponta
└── docs/ARCHITECTURE.md

brasil-lens-frontend/
├── docker-compose.yml            # só o web, para rodar o frontend isolado
├── Dockerfile  nginx.conf        # build do Vite servido por nginx
├── package.json  vite.config.ts  tsconfig.json
└── src/
    ├── api/{client,types,queries}.ts    # contratos tipados + TanStack Query
    ├── features/map/                    # MapView, ChoroplethLayer, Legend, cores
    ├── features/controls/               # seleção de indicador e ano
    ├── features/detail/                 # painel de detalhe do território
    ├── features/views/                  # visualizações salvas (POST/PUT/DELETE)
    ├── components/                      # Select, Feedback
    └── lib/format.ts                    # formatação pt-BR
```

---

## 4. Modelo de dados

### 4.1 A decisão central: entidade territorial única

Avaliei as duas alternativas pedidas.

**Alternativa A — tabelas por nível (`regions`, `states`, `municipalities`).**
Ganha em clareza de atributos específicos. Perde no que importa:
- `indicator_values` precisaria de 3 tabelas de valores (triplicando query, índice e
  código do mapa) **ou** de `(territory_type, territory_id)` polimórfico — que é
  exatamente a estrutura sem FK real que o documento original corretamente quer evitar.
- Geometria, simplificação e índice espacial seriam replicados 3×.
- O endpoint do mapa teria um `if` por nível.
- Adicionar um nível (mesorregião, distrito) seria uma migration + código novo em toda
  a pilha.

**Alternativa B — entidade territorial única (`territories` + `level`).** Escolhida.
- `indicator_values.territory_id` é uma **FK real**, com `ON DELETE CASCADE`.
- Hierarquia por `parent_id` auto-referente: filhos, ancestrais e drill-down são uma
  query só, em qualquer nível.
- Uma tabela de geometria, um índice GiST, um caminho de simplificação.
- Novo nível territorial = novo valor no enum (mais as duas linhas de hierarquia
  ao lado dele) + ingestão. Nada mais muda.

**O custo real da Alternativa B** é que atributos específicos de nível viram colunas
nullable. No MVP são apenas duas (`abbreviation` para UF/região e
`capital_territory_id` para UF/Brasil), o que é um preço baixíssimo. Se um nível
ganhar muitos atributos próprios, a extensão natural é uma tabela satélite
(`state_attributes`) — sem tocar no núcleo.

`capital_territory_id` é FK para `territories`: a capital **é** um município. Guardar o
nome como texto perderia integridade e o código IBGE da capital.

### 4.2 Tabelas

#### `territories`
| Coluna | Tipo | Notas |
|---|---|---|
| `id` | `integer` PK identity | ID interno; não aparece na API |
| `level` | `territory_level` NOT NULL | enum: `country`, `region`, `state`, `municipality` |
| `ibge_code` | `varchar(9)` NOT NULL | **UNIQUE** — identificador canônico externo (`BR`, `3`, `35`, `3549904`) |
| `name` | `varchar(120)` NOT NULL | de Localidades |
| `abbreviation` | `varchar(4)` NULL | `SP`, `SE` |
| `parent_id` | `integer` NULL | **FK** → `territories.id` `ON DELETE RESTRICT` |
| `capital_territory_id` | `integer` NULL | **FK** → `territories.id` `ON DELETE SET NULL` |
| `bbox_west/south/east/north` | `double precision` NULL | preenchido pela ingestão de geometria; evita tocar geometria no drill-down |
| `created_at`/`updated_at` | `timestamptz` NOT NULL | |

- `CHECK`: `level='country'` ⟺ `parent_id IS NULL`.
- `UNIQUE (ibge_code)` — sem colisão entre níveis (1, 2, 7 dígitos + `BR`).
- Índices: `(level, name)` para listagens ordenadas; `(parent_id, name)` para filhos.

#### `territory_geometries`
| Coluna | Tipo | Notas |
|---|---|---|
| `territory_id` | `integer` | PK parte 1, FK → `territories` `ON DELETE CASCADE` |
| `lod` | `geometry_lod` | PK parte 2 — enum: `canonical`, `overview`, `detail` |
| `geom` | `geometry(MultiPolygon, 4326)` NOT NULL | |
| `simplify_tolerance` | `double precision` NULL | `NULL` em `canonical` |
| `vertex_count` | `integer` NOT NULL | diagnóstico da simplificação |
| `dataset_id` | `smallint` NULL | FK → `datasets` (proveniência da malha) |

- **PK `(territory_id, lod)`** — uma geometria por território por nível de detalhe;
  garante idempotência do upsert.
- Índice **GiST** em `geom`.
- Uma tabela em vez de colunas `geom_overview`/`geom_detail`: adicionar um LOD passa a
  ser dado, não schema, e a query do mapa continua sendo `WHERE lod = :lod`.
- `MultiPolygon` é o tipo correto e **verificado**: a malha do IBGE devolve `Polygon` e
  `MultiPolygon` misturados (ilhas, exclaves). A ingestão normaliza tudo com
  `ST_Multi` + `ST_CollectionExtract(.., 3)`.

#### `indicators`
| Coluna | Tipo | Notas |
|---|---|---|
| `id` | `smallint` PK identity | |
| `key` | `varchar(64)` NOT NULL **UNIQUE** | `population`, `gdp_per_capita` — identificador público |
| `name`, `description` | `varchar(160)`, `text` | |
| `unit` | `varchar(24)` NOT NULL | `people`, `km2`, `people/km2`, `BRL` |
| `origin` | `indicator_origin` NOT NULL | `sourced` \| `derived` |
| `decimal_places` | `smallint` NOT NULL | metadado de **formatação** (não de cor) |
| `display_order` | `smallint` NOT NULL | ordem no catálogo |

#### `datasets` — proveniência pragmática
| Coluna | Tipo | Notas |
|---|---|---|
| `id` | `smallint` PK identity | |
| `source` | `varchar(32)` NOT NULL | `ibge` |
| `code` | `varchar(96)` NOT NULL | `agregados/4714/v/93`, `malhas/v3`, `derived/population_density` |
| `name` | `varchar(200)` NOT NULL | rótulo legível |
| `url` | `text` NULL | endpoint/documentação |
| `source_updated_at` | `timestamptz` NULL | quando a **fonte** publicou, quando disponível |
| `UNIQUE (source, code)` | | |

Isto cobre "fonte, dataset/tabela, período, quando importei, quando a fonte mudou" sem
virar data lineage: são 6 colunas e uma FK.

#### `indicator_values` — o núcleo território ↔ indicador ↔ ano ↔ valor
| Coluna | Tipo | Notas |
|---|---|---|
| `territory_id` | `integer` | **PK parte 1**, FK → `territories` `ON DELETE CASCADE` |
| `indicator_id` | `smallint` | **PK parte 2**, FK → `indicators` `ON DELETE CASCADE` |
| `reference_year` | `smallint` | **PK parte 3**, `CHECK` 1900–2100 |
| `value` | `numeric(24,6)` NOT NULL | 24 dígitos porque o PIB nominal do Brasil já está na casa de 10¹³ reais |
| `dataset_id` | `smallint` NOT NULL | FK → `datasets` |
| `ingestion_run_id` | `bigint` NULL | FK → `ingestion_runs` `ON DELETE SET NULL` |
| `created_at`/`updated_at` | `timestamptz` NOT NULL | `created_at` = quando foi importado |

**A PK composta `(territory_id, indicator_id, reference_year)` é a decisão que sustenta
a idempotência**: ela é a chave natural, dispensa surrogate (nada referencia um valor)
e serve de índice perfeito para o overview (`WHERE territory_id = ?`).

`value` é `NOT NULL` e **ausência de linha = ausência de dado**. Não existe linha com
valor nulo; isso mantém `MAX(reference_year)` honesto (um ano só é "disponível" se
existir dado) e as estatísticas corretas sem filtros extras.

Índice adicional, ditado pela query do mapa:
```sql
CREATE INDEX ix_indicator_values_indicator_year
  ON indicator_values (indicator_id, reference_year) INCLUDE (territory_id, value);
```
Cobre a query do mapa e a resolução de `latest` com **index-only scan**.

#### `ingestion_runs`
`id`, `job`, `source`, `dataset_code`, `status` (`running|succeeded|partial|failed`),
`started_at`, `finished_at`, `records_processed`, `records_written`, `records_failed`,
`error`, `details jsonb`. Índice `(job, started_at DESC)`.

#### `saved_views`

A única tabela **escrita pelo usuário**; todas as outras são preenchidas pela ingestão.
Guarda um recorte do mapa (nível, pai, indicador, ano, classes) sob um nome.

| Coluna | Tipo | Notas |
|---|---|---|
| `id` | `integer` PK identity | ID interno; não aparece na API |
| `public_id` | `uuid` NOT NULL | **UNIQUE** — identificador público, o que vai na URL de `PUT`/`DELETE` |
| `name` | `varchar(80)` NOT NULL | **UNIQUE**, `CHECK` não-branco |
| `description` | `text` NULL | opcional |
| `level` | `territory_level` NOT NULL | mesmo enum de `territories` |
| `parent_code` | `varchar(9)` NULL | código IBGE do pai; `CHECK` obriga quando `level = 'municipality'` |
| `indicator_key` | `varchar(64)` NOT NULL | chave pública do indicador |
| `reference_year` | `smallint` NULL | **NULL = `latest`**; `CHECK` 1900–2100 |
| `classes` | `smallint` NOT NULL | `CHECK` 2–9 |
| `created_at`/`updated_at` | `timestamptz` NOT NULL | |

**Sem FK para `territories` nem para `indicators`, de propósito.** Uma visualização é um
*marcador de navegação*, não um vínculo de integridade: com FK `ON DELETE CASCADE`, uma
reingestão que recriasse um indicador apagaria em silêncio a visualização do usuário; com
`RESTRICT`, ela travaria a reingestão. A existência do território e do indicador é
validada **na escrita**, no serviço, onde a mensagem de erro pode ser útil (`404` com o
código que não existe) — e a regra "município exige pai" é dupla: `400` no serviço e
`CHECK` na tabela, para que nenhuma visualização impossível de abrir chegue ao banco por
qualquer caminho.

O identificador público é `uuid` e não o serial: é ele que aparece na URL, e um serial
exposto convidaria à enumeração e vazaria a contagem de registros.

### 4.3 Semântica de `latest`

`latest` = `MAX(reference_year)` **daquele indicador dentro do escopo consultado**.

- Mapa (`level=state`): o maior ano com dado para aquele indicador **entre os estados**.
- Overview (um território): o maior ano com dado **daquele território**, por indicador —
  resolvido com `DISTINCT ON (indicator_id) ... ORDER BY indicator_id, reference_year DESC`.

Consequência intencional: dois indicadores na mesma tela podem exibir anos diferentes.
Por isso **todo valor retornado carrega seu próprio `year`**.

---

## 5. Estratégia geoespacial

| LOD | Tolerância | Uso | Origem |
|---|---|---|---|
| `canonical` | — | verdade oficial, nunca servida ao browser | IBGE malhas `qualidade=maxima` |
| `overview` | 0.02° (~2 km) | mapa do Brasil inteiro (estados/regiões) | `ST_SimplifyPreserveTopology` na ingestão |
| `detail` | 0.002° (~200 m) | municípios de um estado | idem |

Pipeline por feature, **na ingestão**:
```sql
ST_CollectionExtract(ST_Multi(ST_MakeValid(geom_original)), 3)   -- canonical
ST_CollectionExtract(ST_Multi(ST_MakeValid(
  ST_SimplifyPreserveTopology(canonical, :tolerance))), 3)       -- LOD derivado
```
`ST_SimplifyPreserveTopology` (e não `ST_Simplify`) porque `ST_Simplify` pode produzir
geometria inválida e buracos visíveis entre municípios vizinhos.

**Nenhuma simplificação ocorre em tempo de requisição.** O endpoint do mapa faz
`ST_AsGeoJSON(geom)` sobre geometria já simplificada, que é leitura pura.

`bbox_*` em `territories` é preenchido na ingestão a partir da geometria canônica, para
que o drill-down (ajustar o mapa ao estado clicado) não toque em geometria nenhuma.

Índices: GiST em `territory_geometries.geom` (obrigatório para qualquer predicado
espacial futuro: viewport, point-in-polygon, vizinhança).

**Carregamento progressivo:** a aplicação abre com 27 estados em `overview`. Municípios
só são buscados quando um estado é selecionado, em `detail`, e apenas os daquele estado.
`level=municipality` **sem** `parent` é rejeitado com HTTP 400 — a política de
performance é aplicada pela API, não confiada ao cliente.

---

## 6. Estratégia de ingestão

Quatro jobs, todos idempotentes, todos com registro em `ingestion_runs`:

```bash
python -m app.jobs.seed_indicators      # catálogo (upsert por key)
python -m app.jobs.import_territories   # Localidades → hierarquia BR/região/UF/município
python -m app.jobs.import_geometries     # malhas → canonical + overview + detail
python -m app.jobs.import_indicators     # agregados v3 → valores + derivados
python -m app.jobs.bootstrap             # os quatro na ordem correta
```

Fluxo de `import_indicators`:
1. **Consultar** `/api/v3/agregados/{t}/periodos/{p}/variaveis/{v}?localidades=...`
2. **Validar** com modelos Pydantic do provider (`AggregateResponse`) — resposta fora do
   contrato falha alto, não silenciosamente.
3. **Converter** para `IndicatorObservation(ibge_code, reference_year, value)`.
4. **Normalizar**: sentinelas (`"..."`, `"-"`, `".."`, `"X"`) → descartadas;
   `Mil Reais` → BRL via `value_multiplier=1000` declarado no dataset.
5. **Persistir** com `INSERT ... ON CONFLICT (territory_id, indicator_id, reference_year)
   DO UPDATE` — uma transação por (dataset × escopo).
6. **Registrar** contagens e status no `ingestion_run`.

**Idempotência** vem de três mecanismos combinados: PK/UNIQUE natural em todas as
tabelas de destino, `ON CONFLICT DO UPDATE` em todo upsert e nenhuma coluna sequencial
usada como chave de negócio. Rodar duas vezes atualiza `updated_at` e não cria linha.

**Falha parcial:** cada dataset/UF commita separadamente. Se a malha de 3 UFs falhar, as
24 restantes permanecem gravadas e o run termina com `status='partial'`, listando os
escopos falhos em `details`. Isso é preferível a perder 60 MB de download por um 503.

Observações de normalização fixadas em código:
- **Nomes vêm só de Localidades** (SIDRA devolve `"Adamantina - SP"`).
- **As fontes do IBGE divergem entre si.** O município 5101837 (Boa Esperança do
  Norte/MT) existe na API Localidades mas ainda não está na malha territorial. O
  job reporta a lacuna em `ingestion_runs.details.missing_geometry` e o mapa não
  o desenha (INNER JOIN na geometria) — em vez de esconder o fato.
- Território desconhecido em um valor → contabilizado como `records_failed`, não cria
  território fantasma. A ordem dos jobs garante que isso não aconteça normalmente.

### Indicadores derivados

| Indicador | Forma | Por quê |
|---|---|---|
| `population_density` | razão (população ÷ área) | Depende só de dados que mudam na ingestão. Persistir mantém um único caminho de leitura (mapa e overview não sabem que ele é derivado), permite índice, ordenação e classificação iguais aos demais. |
| `gdp_per_capita` | razão (PIB ÷ população) | Obrigatório: verificado que a tabela 5938 não tem variável per capita. |
| `urbanization_rate` | razão (pop. urbana ÷ população × 100) | A fonte publica o percentual só para 2022 (v. 1000093); derivando, ele acompanha também o Censo 2010. |
| `population_growth` | crescimento anualizado | Nenhuma tabela publica a série de variação anual por território. |
| `gdp_share_national` | participação no total nacional | O IBGE publica (v. 496), mas só para o recorte dele; derivando, a definição fica sob nosso controle e é conferida contra a publicada. |

**Três formas, não uma genérica.** Tentar unificá-las em "derivação com duas
entradas" não funcionaria: crescimento compara o mesmo indicador em **anos**
diferentes e participação compara o mesmo indicador em **territórios**
diferentes. São três comandos SQL de ~20 linhas em `services/derived.py`, com o
mesmo upsert idempotente e o mesmo registro de proveniência. O job não conhece
nenhum deles: ele resolve dependências pelo `spec.dependencies` e delega.

Regra temporal explícita: para o ano *Y*, o denominador é o valor do indicador-base com
o **maior `reference_year` ≤ Y**; se não existir nenhum, o mais próximo acima —
que, quando todos os anos disponíveis são posteriores a Y, é o mais antigo
deles. Isso evita que
`population_density(2022)` fique sem valor só porque a área foi publicada em 2021.

Cálculo sob demanda foi rejeitado: obrigaria o endpoint do mapa a conhecer a fórmula,
impediria índice sobre o resultado e replicaria a regra temporal em cada query.

---

## 7. API (MVP)

Envelope de erro único: `{"error": {"code", "message", "details"}}`.

| Método | Rota | Uso |
|---|---|---|
| GET | `/api/v1/health` | liveness (sem banco) |
| GET | `/api/v1/health/ready` | readiness (`SELECT 1` + PostGIS) |
| GET | `/api/v1/indicators` | catálogo + cobertura (`availableYears`, `latestYear`) |
| GET | `/api/v1/indicators/{key}` | um indicador |
| GET | `/api/v1/territories?level=&parent=&search=&limit=&offset=` | listagens (regiões, UFs, municípios de uma UF) |
| GET | `/api/v1/territories/{ibge_code}` | território + pai + bbox |
| GET | `/api/v1/territories/{ibge_code}/overview` | **projeção para a tela de detalhe** |
| GET | `/api/v1/territories/{ibge_code}/indicators` | séries históricas (base para gráficos futuros) |
| GET | `/api/v1/map?level=&parent=&indicator=&year=&lod=` | **projeção para o mapa** |
| GET | `/api/v1/views` | visualizações salvas, mais recentes primeiro |
| POST | `/api/v1/views` | cria uma visualização — `201` + `Location` |
| GET | `/api/v1/views/{id}` | uma visualização |
| PUT | `/api/v1/views/{id}` | **substitui** a visualização inteira — `200` |
| DELETE | `/api/v1/views/{id}` | remove — `204` |

Quatorze rotas. O mapeamento da lista original está em §1.2.

As nove primeiras são projeções de leitura sobre o que a ingestão trouxe; as cinco de
`/views` são a única família que escreve. A separação é explícita nas dependências:
`get_session` abre uma sessão que **nunca** commita (uma escrita acidental em um `GET`
não passa despercebida) e `get_write_session` abre uma transação por requisição, com
commit no fim do handler e rollback em qualquer exceção — inclusive nas de domínio, que
viram resposta de erro. Uma validação que falha depois de um `flush` não deixa linha órfã.

`PUT` e não `PATCH` porque a entidade tem seis campos: substituição completa mantém um só
caminho de escrita, um só conjunto de validações e nenhuma semântica de "campo ausente
significa manter". `DELETE` de um id inexistente devolve `404`, não `204`: apagar algo que
nunca existiu é erro do cliente, e silenciar isso esconderia um id errado na interface.

`year` no contrato é string (`"latest"` ou `"2022"`), igual à rota `/map` — o cliente
guarda e reenvia o mesmo valor que já usa no seletor de ano. A tradução para
`reference_year` (NULL quando `latest`) fica no serviço, não no cliente.

### `GET /api/v1/map?level=state&indicator=gdp_per_capita&year=latest`

GeoJSON válido com membros estrangeiros — consumido direto pelo react-leaflet:

```json
{
  "type": "FeatureCollection",
  "scope":     { "level": "state", "parent": null, "lod": "overview", "count": 27 },
  "indicator": { "key": "gdp_per_capita", "name": "PIB per capita", "unit": "BRL",
                 "decimalPlaces": 2, "year": 2023, "requestedYear": "latest",
                 "availableYears": [2010, 2011, "…"] },
  "statistics":{ "min": 21458.4, "max": 128000.1, "mean": 41230.7,
                 "median": 38010.2, "count": 27, "missing": 0 },
  "classification": { "method": "quantile", "classes": 5,
                      "breaks": [21458.4, 30110.0, 38010.2, 47900.5, 128000.1] },
  "bbox": [-73.99, -33.75, -34.79, 5.27],
  "features": [
    { "type": "Feature", "id": "35",
      "properties": { "ibgeCode": "35", "name": "São Paulo", "level": "state",
                      "abbreviation": "SP", "parentCode": "3", "parentName": "Sudeste",
                      "value": 62000.5, "normalizedValue": 0.38, "classIndex": 3 },
      "geometry": { "type": "MultiPolygon", "coordinates": [] } }
  ]
}
```

Decisões do contrato:
- Territórios **sem dado aparecem** com `value: null`, `normalizedValue: null`,
  `classIndex: null` — o mapa precisa desenhar o país inteiro; o frontend estiliza
  "sem dado".
- `statistics` e `classification` consideram apenas valores não nulos; com 0 valores,
  ambos vêm `null`.
- `bbox` já vem pronto (evita o cliente iterar coordenadas para dar `fitBounds`)
  e é **omitido** quando não há extensão conhecida: a RFC 7946 exige array
  quando o campo existe, e `"bbox": null` faria clientes tipados recusarem a
  resposta. Os outros campos nulos permanecem — `value: null` é informação.
- A API **não** devolve cor, paleta ou tema.

### `GET /api/v1/territories/35/overview`

```json
{
  "ibgeCode": "35", "name": "São Paulo", "level": "state", "abbreviation": "SP",
  "parent":  { "ibgeCode": "3", "name": "Sudeste", "level": "region" },
  "capital": { "ibgeCode": "3550308", "name": "São Paulo" },
  "childrenCount": 645,
  "childrenLevel": "municipality",
  "bbox": [-53.11, -25.31, -44.16, -19.78],
  "indicators": [
    { "key": "population", "name": "População", "unit": "people",
      "decimalPlaces": 0, "value": 44411238, "year": 2022,
      "source": "IBGE — Censo 2022", "origin": "sourced" },
    { "key": "gdp", "name": "PIB", "unit": "BRL", "decimalPlaces": 0,
      "value": null, "year": null, "source": null, "origin": "sourced" }
  ]
}
```

Escolhi **lista** em vez do objeto `{"population": {...}}` do esboço original: preserva
a ordem de exibição do catálogo, permite adicionar indicador sem mudar o shape e evita
que o cliente tenha chaves camelCase sincronizadas à mão com `indicator.key`.

### Política de ausência e erro

| Situação | Resposta |
|---|---|
| Indicador sem valor para o território | `200`, `value: null`, `year: null` |
| Ano pedido sem dado | `200`, valores `null`, `indicator.year` = ano pedido |
| `year=latest` e indicador sem dado algum | `200`, `indicator.year: null`, `statistics: null` |
| `ibge_code` inexistente | `404 territory_not_found` |
| `indicator` inexistente | `404 indicator_not_found` |
| `level=municipality` sem `parent` | `400 invalid_parameter` (proteção de payload) |
| `parent` de nível incompatível com `level` | `400 invalid_parameter` |
| tipo/valor inválido de query param | `422 validation_error` (mesmo envelope) |

---

## 8. Performance

Onde os gargalos realmente estão, medidos ou calculados:

| # | Gargalo | Mitigação | Custo |
|---|---|---|---|
| 1 | **Tamanho da geometria** — malha municipal de MG: 8,8 MB / 401.746 vértices (medido) | LOD pré-computado na ingestão + `GZipMiddleware` (GeoJSON comprime ~5×) | nenhum em runtime |
| 2 | Simplificar por requisição | proibido: só leitura de geometria já simplificada | — |
| 3 | Junção valor × geometria no cliente | uma query, uma resposta (`/map`) | — |
| 4 | Resolução de `latest` como query extra | CTE única na mesma query do mapa | — |
| 5 | Estatísticas e quantis | `percentile_cont` em SQL sobre ≤853 linhas | µs |
| 6 | Serialização JSON | `ORJSONResponse` como default | — |
| 7 | Requisição inicial repetida (mesmo `indicator`+`year` para todos) | cache TTL **em processo** (dict + TTL, 64 entradas) + `Cache-Control` | ~40 linhas, zero infra |
| 8 | Sequential scan em valores | índice `(indicator_id, reference_year) INCLUDE (territory_id, value)` | 1 índice |

**Materialized view: analisada e recusada por ora.** O endpoint do mapa lê 27 (ou ≤853)
linhas por índice e faz `ST_AsGeoJSON` sobre geometria já reduzida. O custo dominante é
**transferir bytes**, não calcular — e uma MV não reduz bytes. A pré-computação que
realmente importa (simplificação) já foi movida para a ingestão. Uma MV só passa a se
justificar quando aparecer agregação hierárquica real (ex.: somar municípios para
compor região) ou ranking global sobre muitas dimensões. Documentar o gatilho é mais
honesto que construir a estrutura agora.

Índices definidos a partir das queries reais:

| Query | Índice |
|---|---|
| `WHERE ibge_code = ?` | `UNIQUE (ibge_code)` |
| `WHERE level=? ORDER BY name` | `(level, name)` |
| `WHERE parent_id=? ORDER BY name` | `(parent_id, name)` |
| mapa: `indicator_id`+`reference_year` | `(indicator_id, reference_year) INCLUDE (territory_id, value)` |
| overview: `territory_id` | PK `(territory_id, indicator_id, reference_year)` |
| geometria por LOD | PK `(territory_id, lod)` |
| espacial (futuro) | GiST `(geom)` |

---

## 8.1 Resultados medidos

A fundação foi executada contra as fontes reais do IBGE. Números observados:

**Volume ingerido**

| O quê | Quantidade |
|---|---|
| Territórios | 1 país + 5 regiões + 27 UFs + 5.571 municípios |
| Geometrias | 5.603 territórios × 3 LODs |
| Valores de indicador | 526.395 linhas |
| Vértices municipais | 2.583.201 (`canonical`) → 647.097 (`detail`) → 85.798 (`overview`) |
| Duração | territórios 1,4 s · geometrias 41 s · indicadores 2 min 13 s |

**Idempotência** — segunda execução com os mesmos dados:
257.603 observações processadas → **0 linhas gravadas**.

**Latência e payload da API**

| Endpoint | Sem gzip | Com gzip | Latência |
|---|---|---|---|
| `/map?level=state&indicator=…` | 136 KB | 45 KB | 16 ms |
| `/map?level=municipality&parent=35` (645) | 1,1 MB | 300 KB | ~135 ms |
| `/map?level=municipality&parent=31` (853) | 2,0 MB | 556 KB | ~256 ms |
| `/territories/35/overview` | 1,3 KB | — | 5 ms |
| `/indicators?level=state` | 2,1 KB | — | 9 ms (1 ms em cache) |

Duas consultas foram reescritas por medição, não por intuição:

* **Overview a 204 ms para devolver 1,3 KB.** A causa era calcular cobertura
  temporal (agregado sobre meio milhão de valores) em uma tela que não usa esse
  dado. Separar `list_definitions` de `list_catalog` levou o endpoint a 5 ms.
* **Catálogo a 199 ms.** `ARRAY_AGG(DISTINCT ... ORDER BY ...)` sobre o join
  forçava um sort de 509.601 linhas com 4,3 MB de spill em disco. Extrair a
  cobertura com `GROUP BY` sobre o índice `(indicator_id, reference_year)` e só
  então agregar em array levou a consulta a 56 ms — e o cache TTL a 1 ms.

**Distribuição dos dados**, que valida a escolha de quantis: entre os 645
municípios de São Paulo, a população mediana é 13.454 e a máxima 11.911.337. Com
intervalos iguais, 644 dos 645 cairiam na primeira classe.

---

## 8.2 Correções da segunda fase

Revisão de refinamento. O que foi medido e corrigido, com os números:

| Problema | Antes | Depois |
|---|---|---|
| **Prefetch no hover sem debounce** — uma requisição por polígono cruzado pelo cursor | 40 polígonos varridos = **40 requisições** | 40 polígonos = **1 requisição** |
| **Cache do mapa sem teto de volume** — coleções municipais desserializadas ocupam ~7 MB cada | 40 entradas: RSS de 92 MB → **378 MB** | as mesmas 40: 92 MB → **115 MB** |
| **Catálogo e overview recalculando cobertura** | overview 204 ms / catálogo 199 ms | **5 ms** / 56 ms (1 ms em cache) |
| **`lod=canonical` acessível pela API** | 1 MB nos estados, 8,8 MB nos municípios de MG | recusado com 422 |
| **`level=country&parent=X`** | 200 com zero features | 400 `invalid_parameter` |
| **Folga fixa de 336 px no `fitBounds`** | em 375 px de viewport sobravam 15 px úteis e o mapa ia para o oceano | folga proporcional, limitada a 40% do mapa |
| **`style` recriado a cada render** | `setStyle` em todas as feições a cada re-render, apagando o hover | memoizado por `(coleção, seleção)` |
| **Ano redefinido por efeito na troca de escopo** | descartava a escolha do usuário e gerava uma requisição extra | ano derivado e preservado quando existe no nível |

Sobre o cache: o critério passou a ser **volume de features**, não nível
territorial. Isso expressa a restrição real (memória ∝ geometria) e não precisa
de manutenção quando um nível novo aparecer. As projeções realmente
compartilhadas — a visão inicial do país, que todo usuário pede — continuam
cacheadas com 100% de acerto em repetição; as municipais, que são cauda longa
(cada usuário abre um estado diferente), passam a depender apenas do
`Cache-Control` do cliente.

### Um falso positivo, registrado

Durante a revisão, a API foi derrubada para testar o comportamento de erro e a
interface ficou em esqueleto de carregamento indefinidamente, sem mensagem. A
conclusão imediata — "o tratamento de erro está quebrado" — estava **errada**.

A causa real: o retryer do TanStack Query pausa a consulta quando
`focusManager.isFocused()` é falso, independentemente de `networkMode`, e a aba
usada no teste estava com `document.visibilityState === 'hidden'`. Forçando
`focusManager.setFocused(true)`, a consulta falhou com `TypeError: Failed to
fetch` e a tela exibiu corretamente *"Não foi possível falar com a API"*.

Ou seja: o comportamento está correto, e pausar requisições em aba de fundo é
até desejável. A configuração `networkMode: 'always'`, que havia sido aplicada
com base no diagnóstico errado, foi revertida — o padrão `'online'` pausa quando
o aparelho está de fato sem rede e retoma na reconexão, o que é melhor que
falhar.

Fica o registro porque o caminho importa: um sintoma real pode ter causa no
ambiente de teste, e mudar configuração de biblioteca por hipótese não
verificada é como se introduz regressão.

Uma correção **não** foi feita, deliberadamente: trocar de indicador com
centenas de municípios na tela recria a camada GeoJSON inteira, porque o
componente do react-leaflet não reage a troca de `data`. Medido em 63 ms para
417 municípios (~130 ms para 853). Evitar a remontagem exigiria manter a camada
montada e reestilizar por consulta a uma referência externa, com risco de
geometria obsoleta — complexidade que não se paga para um custo abaixo do
limiar de percepção em uma ação deliberada do usuário.

---

## 8.3 Evolução para novas camadas de dados

Avaliação de acoplamento para camadas meteorológicas, hidrológicas e
ambientais. **Nada foi implementado**; o objetivo é verificar se as decisões
atuais bloqueiam algo.

| Necessidade futura | Situação | Caminho |
|---|---|---|
| Novos providers | **Livre** | `providers/<fonte>/` devolvendo `IndicatorObservation` já é a fronteira. Domínio, schema e API não mudam. |
| Camadas temáticas | **Livre** | Uma coluna `theme` em `indicators` + filtro opcional na API. Migration trivial. |
| Ativar/desativar conjuntos | **Livre** | O endpoint do mapa já é **por indicador**. Cada camada é uma requisição independente — com cache, TTL e domínio de falha próprios. É o desenho correto para cadências diferentes, e ele já existe. |
| Combinar histórico e recente no mesmo mapa | **Livre** | Duas chamadas a `useMapLayer` no cliente. Nenhuma mudança de contrato. |
| Cadências de atualização distintas | **Livre** | `TTLCache.set` aceita TTL por entrada (adicionado nesta fase, 3 linhas). Um dado horário expira em minutos sem afetar o censitário. |
| **Dados sub-anuais** | **Único acoplamento real** | Ver abaixo. |
| Dados espaciais mais dinâmicos (estações, grades) | **Livre, com ressalva** | Ver abaixo. |

### O acoplamento real: granularidade temporal

`indicator_values` tem PK `(territory_id, indicator_id, reference_year)` com
`reference_year smallint`. Dado horário ou diário **não cabe** aí.

A tentação seria generalizar agora para `observed_at timestamptz`. Seria um
erro: séries anuais e séries temporais têm padrões de acesso, retenção e índice
genuinamente diferentes, e a resolução de `latest` de um censo não se parece com
a de uma medição de vazão. Unificar as duas pagaria o custo da mais complexa em
todas as consultas da mais simples.

O caminho, quando a necessidade existir, é uma tabela irmã:

```
indicator_observations
  territory_id   FK → territories
  indicator_id   FK → indicators
  observed_at    timestamptz
  value          numeric
  dataset_id     FK → datasets
  PK (territory_id, indicator_id, observed_at)
```

Mais uma coluna `temporal_grain` em `indicators` (`'year' | 'instant'`), que diz
à camada de leitura qual tabela consultar. `indicators`, `territories`,
`datasets`, `territory_geometries` e toda a camada de providers são reaproveitados
sem alteração — é por isso que este caminho é uma extensão e não uma reescrita.

### A ressalva: dados que não nascem territoriais

O modelo atual assume que **todo valor pertence a um território**. Medições
meteorológicas e hidrológicas nascem em estações e grades, não em polígonos
municipais. A extensão correta não é forçá-las em `indicator_values`, e sim:

1. observações cruas em tabela própria, com geometria própria (ponto ou grade) —
   o padrão de `territory_geometries` (chave composta + índice GiST) serve de
   precedente, não de tabela a reutilizar;
2. agregação espacial **na ingestão**, produzindo valores por território que
   entram no modelo existente.

Isso mantém o princípio central intacto: o trabalho caro acontece antes da
requisição, e o frontend continua recebendo uma projeção pronta.

### A interface com várias camadas

Mesmo com dez camadas, a interface não deve virar um painel de interruptores. O
mecanismo já está no lugar: existe **um** seletor de indicador. Com temas, ele
vira um seletor agrupado (`<optgroup>` por tema) — continua sendo um controle,
não dez. Camadas de contexto que façam sentido sobrepor (chuva sobre densidade,
por exemplo) pedem no máximo uma segunda seleção contextual, revelada quando
houver mais de uma camada disponível, nunca um painel permanente.

---

## 8.4 Terceira fase: novos indicadores e periodicidades

Nove indicadores novos entraram **sem migration, sem endpoint novo e sem tela
nova**: crescimento populacional, renda domiciliar per capita, taxa de
desemprego, taxa de urbanização, PIB por setor (agropecuária, indústria e
serviços) e participação no PIB nacional — mais `urban_population`, que é a base
censitária da urbanização e vale como indicador próprio (o mesmo papel que
`area_km2` já tinha).

O que a fundação previa e se confirmou: o catálogo é lido da API, então os nove
apareceram **sozinhos** no seletor do frontend; o endpoint do mapa é por
indicador, então não houve `if` novo em nenhuma query.

O que a fundação **não** previa e exigiu decisão:

| Achado na fonte | Decisão |
|---|---|
| **Periodicidade sub-anual.** A taxa de desocupação (6468) é trimestral e `indicator_values` tem chave por ano. Quatro trimestres viram quatro linhas com a mesma chave — e `ON CONFLICT DO UPDATE` recusa afetar a mesma linha duas vezes. | Redução para ano **na normalização do provider**, não no SQL nem no banco: os períodos do ano viram a média anual. Nenhuma camada acima sabe que a fonte é trimestral. |
| **A média dos trimestres não é a média anual do IBGE.** Medido em 2024: oficial 6,6%, média dos quatro trimestres 6,85% (o instituto calcula sobre a amostra do ano). | Duas fontes para o mesmo indicador, **na ordem** que o registro já suportava: a trimestral entra primeiro e dá cobertura ao ano em curso; a anual oficial (4562) entra depois e prevalece nos anos fechados. Zero código novo — é o mesmo mecanismo que faz o Censo prevalecer sobre a estimativa. |
| **População urbana não é variável, é categoria** da variável 93 (`classificacao=1[1]`). | Um campo `classification` no registro e no `AggregateQuery`. Ele entra também no `dataset_code`: a mesma tabela/variável devolve população total ou urbana conforme o recorte, e as duas precisam de proveniências distintas. |
| **Serviços vêm partidos em duas variáveis** (6575 privados, 525 administração pública). Usar só a primeira subestimaria o setor em ~1/5 do VAB. | As variáveis de uma consulta são **somadas** por (território, período) na normalização. Um teste confere que os três setores fecham o VAB total publicado (v. 498). Se uma parcela vier indisponível, a soma é descartada em vez de virar um número menor com a mesma aparência de um valor completo. |
| **A abertura setorial fica dois anos atrás do PIB total** (setores até 2021, total até 2023). | Nada a fazer além de não inventar: os anos com sentinela são descartados, e `availableYears` expõe a cobertura real de cada indicador. O mapa de PIB abre em 2023 e o de PIB — Indústria em 2021, cada um com o próprio ano no contrato. |
| **PNAD Contínua no nível municipal só apura as capitais** (verificado: `N6[N3[35]]` devolve 1 município de 645). | Renda e desemprego param em UF. Um mapa municipal com 1 de 645 pintados seria pior que um mapa vazio: as estatísticas e os quantis sairiam de uma amostra de um. |
| **Crescimento populacional em ano censitário mede a revisão do Censo**, não movimento demográfico: em 2022 compara o Censo (203,1 mi) com a estimativa de 2021 (213,3 mi). | Mantido como a série publicada diz, e **dito na descrição do indicador**, que aparece no painel. Filtrar seria uma escolha editorial silenciosa. O `latest` cai no ano corrente, onde a comparação é homogênea. |

### Indisponibilidade, ponta a ponta

Com indicadores que não existem em todos os níveis, o caminho de "sem dado"
deixou de ser exceção rara e virou caso corrente. Ele já estava desenhado e foi
exercitado: `/map?level=municipality&parent=35&indicator=unemployment_rate`
devolve 200 com 645 feições, `value: null`, `statistics: null` e
`classification: null`; a legenda diz "Sem dados para este recorte" e o mapa
desenha o estado inteiro na cor de ausência. A única adição na interface foi uma
linha no painel existente explicando *por quê* — sem tela nova e sem mudar o
mapa.

---

## 9. Como estender

**Novo indicador vindo de fonte já suportada** — sem código novo de infraestrutura:
1. Adicionar a entrada em `providers/ibge/datasets.py` (tabela, variável, níveis,
   multiplicador, unidade).
2. Adicionar o indicador em `jobs/seed_indicators.py`.
3. `python -m app.jobs.seed_indicators && python -m app.jobs.import_indicators`.

Isto foi **validado na prática**: as fontes censitárias de 2010 (tabela 202 para
população, 1301 para área) entraram como duas entradas no registro, sem alterar
nenhuma linha de código. A ingestão gravou 11.196 valores novos, reescreveu 0
linhas das fontes já existentes e recalculou os derivados sozinha — inclusive
corrigindo a densidade de 2010, que passou a usar a área de 2010 em vez da de
2022.

**Novo indicador derivado:** uma entrada em `services/derived.py` declarando numerador,
denominador e fator; a regra temporal já é compartilhada.

**Nova fonte (SICONFI, IPEA, DataSUS):** um módulo em `providers/<fonte>/` que devolve
`IndicatorObservation`, um `dataset` novo e um job (ou um parâmetro no job existente).
O domínio, o schema e a API **não mudam** — é justamente por isso que
`IndicatorObservation` existe como fronteira.

**Novo nível territorial:** novo valor no enum `territory_level`, as duas linhas de
hierarquia declaradas ao lado dele (`REQUIRES_PARENT` e `EXPECTED_PARENT_LEVEL`, em
`app/models/territory.py`) e a ingestão. Modelo, mapa, visualizações salvas e API já
são genéricos por nível — `/map` e `/views` leem a mesma hierarquia, em vez de cada
serviço manter a sua.
