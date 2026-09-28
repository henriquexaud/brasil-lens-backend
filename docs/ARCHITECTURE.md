# Arquitetura

A API é um monólito modular: `providers` convertem fontes externas em registros internos; `jobs` ingerem dados; `repositories` consultam PostgreSQL/PostGIS; `services` aplicam regras; `api/v1` expõe os contratos HTTP.

## Componentes e comunicação

```mermaid
flowchart LR
    Web["React / Leaflet — interface principal"] -->|"REST: GET, POST, PUT, DELETE"| API["FastAPI — API própria"]
    API -->|"SQL / consultas espaciais"| DB["PostgreSQL / PostGIS"]
    API --> Cache["Redis opcional"]
    API -->|"REST / JSON / GeoJSON"| Fontes["IBGE, Open-Meteo, INMET, CEMADEN, INPE, ANA"]
```

Frontend e backend têm [repositórios](https://github.com/henriquexaud/brasil-lens-frontend) [separados](https://github.com/henriquexaud/brasil-lens-backend), com Dockerfiles e READMEs próprios. Os dados das fontes externas são processados e integrados ao domínio pela API; o navegador consome apenas contratos próprios, com exceção dos tiles do mapa base e da camada WMS de focos. O Compose completo fica no backend; um Compose adicional na pasta de trabalho permite desenvolver clones locais juntos.

## Domínio territorial

`territories` mantém país, regiões, estados e municípios numa hierarquia única, relacionada por `parent_id`. `ibge_code` preserva os códigos oficiais. As colunas `bbox_*` aceleram o enquadramento do mapa e os índices normalizados apoiam busca sem acentos.

`territory_geometries` guarda as malhas canônicas do IBGE e duas simplificações (`overview`, `detail`). `/map` serve as geometrias simplificadas; a consulta paginada de limites municipais pode servir a malha canônica. O PostGIS usa GiST para consultas espaciais, como viewport e localização por ponto.

Essas tabelas são necessárias à experiência climática e permanecem compartilhadas por busca, navegação, foco de município e camadas ambientais.

A projeção do mapa retorna diretamente as linhas geográficas, sem um objeto intermediário de indicador/ano. O território pai é validado e resolvido em uma única consulta. Detalhes territoriais são montados diretamente no contrato `TerritoryDetail`.

## Fontes e dados ambientais

- IBGE Localidades e Malhas: nomes, códigos e geometrias.
- Open-Meteo: condições e previsão.
- INMET: estações, observações e alertas meteorológicos.
- CEMADEN: alertas geo-hidrológicos.
- INPE: focos de calor.
- ANA/SNIRH: hidrografia.

`datasets` e `ingestion_runs` registram proveniência e execução dos jobs territoriais e ambientais. As tabelas `weather_stations`, `weather_observations` e `weather_alerts` preservam dados temporais de fontes climáticas.

## Cache e atualização

A malha usa cache local e Redis, com chave versionada pela última importação territorial/geográfica e ETag HTTP. A consulta da versão de ingestão é compartilhada por até 60 segundos; quando uma nova versão é observada, os derivados geográficos passam a usar novas chaves. O namespace `v4` isola o contrato atual; chaves de versões anteriores expiram por TTL, sem varredura no startup. Dados meteorológicos têm TTLs próprios. Redis indisponível não impede a consulta ao banco ou às fontes.

O agendador atualiza alertas do INMET e CEMADEN no processo da API; condições e previsões da Open-Meteo são consultadas sob demanda. A ingestão legada de estações do INMET permanece disponível pela CLI e não roda no agendador. A hidrografia pode ser aquecida em segundo plano para evitar o custo da primeira consulta.

## Persistência

As tabelas vigentes mantêm geografia, proveniência de ingestão, estações, observações, alertas e municípios acompanhados. As migrations antigas permanecem no histórico para instalações novas; a `0009` removeu o antigo catálogo estatístico e exclui esses dados ao ser aplicada, então faça backup antes de atualizar um banco anterior a ela.
