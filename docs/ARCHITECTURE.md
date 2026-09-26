# Arquitetura

A API é um monólito modular: `providers` convertem fontes externas em registros internos; `jobs` ingerem dados; `repositories` consultam PostgreSQL/PostGIS; `services` aplicam regras; `api/v1` expõe os contratos HTTP.

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

A malha usa cache local e Redis, com chave versionada pela última importação territorial/geográfica e ETag HTTP. A versão `v4` invalida o contrato antigo; no primeiro startup, o backend remove as chaves Redis da versão anterior. Dados meteorológicos têm TTLs próprios. Redis indisponível não impede a consulta ao banco ou às fontes.

O agendador atualiza as fontes ambientais no processo da API. A hidrografia pode ser aquecida em segundo plano para evitar o custo da primeira consulta.

## Persistência

As tabelas vigentes mantêm geografia, proveniência de ingestão, estações, observações, alertas e municípios acompanhados. A migration `0009` remove tabelas e enums do antigo catálogo estatístico e das coropletas anuais. Ela preserva territórios, geometrias, PostGIS e modelos ambientais.

Antes de aplicar `0009_remove_socioeconomic`, faça backup do banco. O upgrade exclui os dados estatísticos e as visualizações salvas, além dos datasets e registros de ingestão exclusivos desse domínio que não tenham referências geográficas ou ambientais. O downgrade restaura o schema anterior, incluindo constraints, índices e enums; recuperar dados excluídos exige restaurar o backup. As migrations anteriores permanecem no histórico para instalações novas e upgrades de bancos existentes.
