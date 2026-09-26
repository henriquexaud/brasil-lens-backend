# Referência da API

Prefixo: `/api/v1`. O Swagger em `/docs` é gerado a partir das rotas e schemas atuais.

## Rotas

| Método | Rota | Uso |
|---|---|---|
| GET | `/health`, `/health/ready` | Liveness e estado do banco/PostGIS |
| GET | `/territories` | Lista, busca e pagina territórios |
| GET | `/territories/{ibge_code}` | Detalhe geográfico |
| POST | `/territories/locate` | Município que contém latitude/longitude |
| GET | `/map` | GeoJSON territorial simplificado |
| GET | `/weather/*` | Condições, previsão, estações, alertas e geometrias municipais |
| GET | `/fire-hotspots`, `/fire-hotspots/summary`, `/fire-hotspots/identify` | Focos do INPE e resumos por recorte |
| GET | `/hydrography` | Rios e corpos d'água |
| GET, PUT, DELETE, POST | `/me/followed-municipalities` | Lista, segue, deixa de seguir e configura avisos de municípios |

## Mapa

`GET /map?level=state` devolve as geometrias estaduais. Para municípios, o recorte por estado é obrigatório: `GET /map?level=municipality&parent=35`. `lod` aceita `overview` ou `detail`; a geometria canônica permanece apenas no banco para preservar o orçamento de rede.

A resposta GeoJSON inclui metadados do escopo, bounding box quando disponível e features com código IBGE, nome, nível, pai e geometria. ETag e `Cache-Control` permitem reutilizar a malha entre navegações.

## Territórios

`GET /territories?search=porto%20alegre` pesquisa nomes e siglas sem acentos. `POST /territories/locate` recebe `{ "latitude": -30.03, "longitude": -51.22 }`; coordenadas precisas não são cacheadas.

## Atualização e erros

Respostas de erro usam `{ "error": { "code", "message", "details" } }`. A disponibilidade das fontes ambientais pode aparecer no payload como estado atualizado, anterior ou indisponível.
