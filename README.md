# Brasil Lens API

API de clima e meio ambiente do Brasil para o [frontend](https://github.com/henriquexaud/brasil-lens-frontend) do Brasil Lens. Combina a geografia do IBGE (PostGIS) com clima (Open-Meteo), alertas (INMET, CEMADEN), focos de calor (INPE) e hidrografia (ANA).

## Rodar com Docker

Requisitos: Docker e Docker Compose v2.

```sh
git clone https://github.com/henriquexaud/brasil-lens-backend.git
cd brasil-lens-backend
docker compose up --build --wait                       # banco, Redis, API e frontend (build a partir do GitHub)
docker compose run --rm api python -m app.jobs.bootstrap   # ingestão do IBGE, necessária para o mapa
```

- Interface em `http://localhost:5173`; API e Swagger em `http://localhost:8000/docs`.
- Só o backend: `docker compose up --build --wait db redis api`.
- Ingestão mais rápida: `--skip-municipal-geometries` ou `--states 35,31`.

## Configuração

Funciona sem `.env`. Para personalizar, copie `.env.example`. As variáveis mais usadas: portas (`API_PORT`, `WEB_PORT`, `POSTGRES_PORT`), `FRONTEND_CONTEXT` (use `../frontend` para um clone local), `VITE_API_BASE_URL` (exige rebuild), `CORS_ORIGINS` e `WEATHER_REFRESH_ENABLED`. A lista completa está em `app/core/config.py`.

## Documentação

[AGENTS.md](AGENTS.md) é o mapa do projeto: arquitetura, invariantes, domínios, decisões, comandos e testes.
