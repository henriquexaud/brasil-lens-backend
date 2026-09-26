# Brasil Lens API

API que sustenta o mapa de **Clima e Meio Ambiente**. O backend combina a hierarquia e as geometrias do IBGE com dados de clima, alertas, focos de calor e hidrografia.

## Arquitetura

- **FastAPI** expõe contratos tipados e documentação OpenAPI.
- **PostgreSQL/PostGIS** guarda territórios, geometrias, estações, observações, alertas e municípios acompanhados.
- **Redis** complementa o cache local para respostas públicas de mapa e camadas ambientais.
- **Jobs** importam a divisão territorial e as malhas do IBGE; um agendador atualiza as fontes climáticas.

Os códigos IBGE, estados, municípios, bounding boxes e geometrias são dados geográficos compartilhados pelo mapa, pela busca, pela localização e pelos recortes de clima. Eles continuam no produto.

## Executar localmente

Com a stack da raiz:

```sh
docker compose up --build --wait
```

Para importar territórios e geometrias pela primeira vez:

```sh
docker compose run --rm api python -m app.jobs.bootstrap
```

A opção `--skip-municipal-geometries` omite as malhas municipais. Para limitar a importação a estados, use `--states 35,31`.

## API

- `/api/v1/health` e `/api/v1/health/ready`
- `/api/v1/territories`: busca, listagem, detalhe e localização por coordenadas
- `/api/v1/map`: malha GeoJSON simplificada para o mapa
- `/api/v1/weather`: condições atuais, previsão, estações e alertas
- `/api/v1/fire-hotspots`: focos, resumo e identificação no mapa
- `/api/v1/hydrography`: rios e corpos d'água
- `/api/v1/me/followed-municipalities`: municípios acompanhados

O Swagger fica em `/docs`.

## Desenvolvimento

```sh
make test
make lint
```

A suíte contém testes unitários e de integração. Os testes marcados com `db` exigem PostgreSQL/PostGIS com territórios importados.

Veja `docs/ARCHITECTURE.md`, `docs/API_REFERENCE.md` e `docs/DATA_INGESTION.md` para os contratos e o fluxo geográfico/climático.
