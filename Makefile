# Atalhos para o ciclo de desenvolvimento. Tudo roda via docker compose, então
# os comandos equivalentes (listados no README) funcionam também sem `make`.
SHELL := /bin/bash
COMPOSE := docker compose
# Sobreposição com recarga automática e o código do host montado. É a que
# `test`, `lint` e `format` usam: sem o mount elas rodariam sobre o código
# assado na imagem, não sobre o que está em edição.
DEV := docker compose -f docker-compose.yml -f docker-compose.dev.yml

.PHONY: help up up-api dev down logs migrate revision ingest ingest-quick seed test lint format check smoke psql reset

help:
	@grep -E '^[a-z-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

up:             ## Sobe tudo: banco + API + frontend (migrations automáticas)
	$(COMPOSE) up --build --wait

up-api:         ## Sobe só o backend: banco + API
	$(COMPOSE) up --build --wait db api

dev:            ## Sobe tudo com recarga automática da API (uvicorn --reload)
	$(DEV) up --build --wait

down:           ## Derruba tudo (mantém o volume do banco)
	$(COMPOSE) down

logs:           ## Acompanha os logs da API
	$(COMPOSE) logs -f api

migrate:        ## Aplica as migrations (a API já faz isso ao subir)
	$(COMPOSE) run --rm api alembic upgrade head

revision:       ## Cria migration nova: make revision m="mensagem"
	$(DEV) run --rm api alembic revision -m "$(m)"

seed:           ## Popula apenas o catálogo de indicadores
	$(COMPOSE) run --rm api python -m app.jobs.seed_indicators

ingest:         ## Ingestão completa do IBGE (territórios + geometrias + indicadores)
	$(COMPOSE) run --rm api python -m app.jobs.bootstrap

ingest-quick:   ## Ingestão sem geometrias municipais
	$(COMPOSE) run --rm api python -m app.jobs.bootstrap --skip-municipal-geometries

test:           ## Roda os testes (unitários + integração com banco)
	$(DEV) run --rm api sh -c "alembic upgrade head && pytest -q"

lint:           ## Ruff + mypy
	$(DEV) run --rm --no-deps api sh -c "ruff check app tests && ruff format --check app tests && mypy app"

format:         ## Formata o código
	$(DEV) run --rm --no-deps api ruff format app tests

check:          ## Tudo que um CI checaria: testes + lint
	$(MAKE) test && $(MAKE) lint

smoke:          ## Exercita GET/POST/PUT/DELETE contra a API no ar
	./scripts/smoke_crud.sh

psql:           ## Abre um psql no banco
	$(COMPOSE) exec db psql -U brasil_lens -d brasil_lens

reset:          ## Apaga o volume do banco e sobe tudo de novo (schema vazio)
	$(COMPOSE) down -v && $(COMPOSE) up --build --wait
