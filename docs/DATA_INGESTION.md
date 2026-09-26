# Ingestão geográfica e atualização ambiental

## Dados territoriais

A hierarquia vem da API IBGE Localidades; as geometrias vêm da API IBGE Malhas v3. O job `import_territories` grava país, regiões, estados e municípios. `import_geometries` grava a geometria canônica e gera simplificações `overview` e `detail` no PostGIS, junto com os bounding boxes.

A ordem é necessária por causa da FK territorial: territórios primeiro, geometrias depois. Os dois jobs são idempotentes e registram a execução em `ingestion_runs` e a fonte em `datasets`.

```sh
docker compose run --rm api python -m app.jobs.bootstrap
```

Opções:

- `--skip-municipal-geometries`: importa apenas país, regiões e estados.
- `--states 35,31`: limita a importação municipal a São Paulo e Minas Gerais.

## Atualização climática

O scheduler da API atualiza observações do INMET, alertas do INMET e CEMADEN. Condições e previsão da Open-Meteo e focos do INPE são consultados conforme a camada e o recorte solicitados. A hidrografia é servida a partir da camada geográfica consultada ao SNIRH/ANA e pode ser aquecida no startup.

## Integridade

- A FK `territories.parent_id` preserva a hierarquia e `ibge_code` é único.
- A PK `(territory_id, lod)` torna a importação das geometrias repetível.
- A PK de estações e observações evita duplicatas por fonte e instante.
- `dataset_id` preserva proveniência das geometrias e das leituras ambientais.
