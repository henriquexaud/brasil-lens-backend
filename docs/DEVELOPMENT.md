# Guia de Desenvolvimento — Brasil Lens Backend

Este guia fornece orientações para desenvolvedores que desejam contribuir, testar ou estender o backend do **Brasil Lens**.

---

## 1. Estrutura de Diretórios

```
backend/
├── Dockerfile                # Imagem oficial da API (Python 3.12-slim)
├── docker-compose.yml        # Orquestração da aplicação completa
├── docker-compose.dev.yml    # Sobrescrita para desenvolvimento com hot-reload
├── Makefile                  # Atalhos para comandos comuns
├── pyproject.toml            # Dependências e configurações (ruff, mypy, pytest)
├── alembic.ini               # Configuração do banco de dados para migrations
├── alembic/                  # Histórico de migrações de banco
├── app/
│   ├── api/v1/               # Rotas HTTP, dependências e controllers
│   ├── core/                 # Configurações globais, logs e middlewares
│   ├── db/                   # Sessão asyncpg, engine SQLAlchemy e Base
│   ├── jobs/                 # Scripts CLI de ingestão de dados
│   ├── models/               # Entidades ORM (Territory, Indicator, SavedView, etc.)
│   ├── providers/            # Adaptadores de fontes externas (IBGE, Open-Meteo, etc.)
│   ├── repositories/         # Consultas SQL e operadores espaciais PostGIS
│   ├── schemas/              # Modelos Pydantic para validação e serialização
│   └── services/             # Regras de negócio, cálculos derivados e classificação
├── docs/                     # Documentação técnica detalhada
├── scripts/                  # Scripts utilitários e smoke tests
└── tests/                    # Suíte de testes unitários e de integração
```

---

## 2. Padrões e Convenções de Código

- **Tipagem:** Python 3.12 com tipagem estrita via `mypy`. Nenhuma anotação `Any` ou `# type: ignore` deve ser introduzida sem justificativa.
- **Formatação e Lint:** Utilizamos o `ruff` tanto para linting quanto para formatação de código.
- **Nomenclatura:**
  - Módulos, funções e variáveis em `snake_case`.
  - Classes e tipos em `PascalCase`.
  - Tabelas e colunas de banco em `snake_case`.
  - Campos em respostas JSON em `camelCase`.

---

## 3. Testes Automatizados

A suíte de testes é executada com `pytest` e inclui testes unitários (sem dependência de rede) e testes de integração com o PostGIS:

```bash
# Executar todos os testes
make test

# Ou diretamente via Docker:
docker compose -f docker-compose.yml -f docker-compose.dev.yml run --rm api pytest -q

# Executar verificação completa (testes + linter + tipagem):
make check

# Executar smoke test contra a API ativa (testa ciclo CRUD de ponta a ponta):
make smoke
```

---

## 4. Como Estender o Sistema

### 4.1 Adicionar um Novo Indicador do IBGE
1. Declare a especificação do indicador e a tabela correspondente do SIDRA em `app/providers/ibge/datasets.py`.
2. Adicione os metadados do indicador ao catálogo em `app/jobs/seed_indicators.py`.
3. Execute a atualização do catálogo e a ingestão:
   ```bash
   make seed
   docker compose run --rm api python -m app.jobs.import_indicators
   ```

### 4.2 Adicionar um Novo Indicador Derivado
1. Abra o arquivo `app/providers/specs.py`.
2. Adicione uma especificação em `DERIVED_INDICATORS` utilizando uma das classes de derivação:
   - `RatioIndicatorSpec`: para indicadores de razão (ex.: PIB ÷ População).
   - `GrowthIndicatorSpec`: para taxas de crescimento anualizado.
   - `ShareIndicatorSpec`: para participação percentual sobre o total nacional.
3. Execute `python -m app.jobs.import_indicators` para calcular os valores.

### 4.3 Adicionar um Novo Provedor Externo
1. Crie um novo módulo sob `app/providers/<nome_provedor>/`.
2. O adaptador deve transformar o payload externo no tipo padronizado `IndicatorObservation` (definido em `app.providers.records`).
3. Registre o novo provedor em `app/providers/registry.py` associando-o ao contexto de dados adequado (`sociopolitical`, `climate_environmental` ou `biodiversity`).

