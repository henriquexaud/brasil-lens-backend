# Referência da API REST — Brasil Lens

Esta documentação descreve todos os contratos, rotas, convenções e comportamentos da API REST do **Brasil Lens**.

A documentação interativa (Swagger / OpenAPI) também está disponível com o servidor em execução em [`http://localhost:8000/docs`](http://localhost:8000/docs) e via Redoc em [`http://localhost:8000/redoc`](http://localhost:8000/redoc).

---

## 1. Convenções Globais

- **Prefixo base:** `/api/v1`
- **Formato de dados:** JSON (GeoJSON para camadas cartográficas)
- **Identificadores territoriais:** Códigos numéricos oficiais do IBGE em formato string (ex.: `"35"` para São Paulo estado, `"3550308"` para São Paulo município). IDs internos de banco nunca vazam no contrato da API.
- **Campos JSON:** `camelCase` nas requisições e respostas (traduzido automaticamente pelo Pydantic a partir dos modelos Python em `snake_case`).
- **Política de ausência de dados:** Quando um indicador não possui dado para determinado território ou ano, a API responde `HTTP 200` com `value: null` e a geometria correspondente intacta. O território continua desenhado no mapa, cabendo ao frontend a estilização de "sem dado".

### Envelope de Erro Padronizado

Todas as respostas de erro (4xx e 5xx), inclusive validações do Pydantic (422), adotam o mesmo envelope estruturado:

```json
{
  "error": {
    "code": "territory_not_found",
    "message": "Território com código IBGE 9999999 não foi encontrado.",
    "details": {}
  }
}
```

---

## 2. Resumo de Endpoints

| Método | Rota | Descrição |
|---|---|---|
| `GET` | `/health` | Verificação simples de liveness da API |
| `GET` | `/health/ready` | Readiness check detalhado (testa conexão com PostgreSQL e PostGIS) |
| `GET` | `/contexts` | Lista contextos de dados e provedores registrados |
| `GET` | `/indicators` | Catálogo de indicadores disponíveis, níveis e anos de cobertura |
| `GET` | `/indicators/{key}` | Detalhes e metadados de um indicador específico |
| `GET` | `/territories` | Listagem e busca de territórios com paginação e filtro por nível/pai |
| `GET` | `/territories/{ibge_code}` | Dados de um território (nome, nível, pai, capital, bounding box) |
| `GET` | `/territories/{ibge_code}/overview` | Projeção resumida para painel lateral de detalhe do território |
| `GET` | `/territories/{ibge_code}/indicators` | Séries históricas de indicadores do território |
| `GET` | `/map` | Projeção completa do mapa (GeoJSON + valores + estatísticas + quantis) |
| `GET` | `/map/values` | Valores e classes de coropleta sem geometrias (leve para troca de indicador/ano) |
| `GET` | `/views` | Lista visualizações salvas pelo usuário |
| `POST` | `/views` | Cria uma nova visualização salva |
| `GET` | `/views/{id}` | Recupera os parâmetros de uma visualização salva por UUID |
| `PUT` | `/views/{id}` | Atualiza uma visualização salva existente |
| `DELETE` | `/views/{id}` | Exclui uma visualização salva |
| `GET` | `/me/followed-municipalities` | Lista municípios favoritados/seguidos pelo usuário |
| `PUT` | `/me/followed-municipalities/{code}` | Segue um município (cria vínculo) |
| `DELETE` | `/me/followed-municipalities/{code}` | Remove município da lista de seguidos |

---

## 3. O Endpoint do Mapa (`GET /map`)

O endpoint `/map` entrega em uma única requisição tudo o que a camada cartográfica precisa: geometrias simplificadas, valores do indicador, resumo estatístico e limites de classificação por quantil.

### Parâmetros de Consulta (Query Params)

| Parâmetro | Tipo | Padrão | Descrição |
|---|---|---|---|
| `level` | `string` | `state` | Nível territorial: `country`, `region`, `state` ou `municipality`. |
| `parent` | `string` | `null` | Código IBGE do território pai (ex.: `35` para filtrar municípios de SP). **Obrigatório quando `level=municipality`**. |
| `indicator` | `string` | `population` | Chave do indicador socioeconômico. |
| `year` | `string` | `latest` | Ano de referência (`"latest"` seleciona automaticamente o ano mais recente disponível). |
| `lod` | `string` | `overview` | Nível de detalhe geométrico: `overview` (simplificado, ideal para carga inicial) ou `detail` (maior precisão). |
| `classes` | `integer` | `5` | Número de faixas de classificação (de 3 a 7). |

### Exemplo de Resposta (GeoJSON Enriquecido)

```json
{
  "type": "FeatureCollection",
  "scope": {
    "level": "state",
    "parent": null,
    "lod": "overview",
    "count": 27
  },
  "indicator": {
    "key": "gdp_per_capita",
    "name": "PIB per capita",
    "unit": "BRL",
    "year": 2023,
    "requestedYear": "latest",
    "availableYears": [2002, 2003, "...", 2023]
  },
  "statistics": {
    "min": 22020.63,
    "max": 129790.43,
    "median": 41047.91,
    "count": 27,
    "missing": 0
  },
  "classification": {
    "method": "quantile",
    "classes": 5,
    "breaks": [22020.63, 29400.12, 41047.91, 58310.45, 129790.43]
  },
  "bbox": [-73.99, -33.75, -29.3, 5.27],
  "features": [
    {
      "type": "Feature",
      "id": "35",
      "properties": {
        "ibgeCode": "35",
        "name": "São Paulo",
        "level": "state",
        "value": 63884.21,
        "classIndex": 4
      },
      "geometry": {
        "type": "MultiPolygon",
        "coordinates": [...]
      }
    }
  ]
}
```

> **Por que GeoJSON com propriedades extras?**
> Porque o componente `<GeoJSON>` de bibliotecas como Leaflet / react-leaflet aceita a resposta nativamente, desenhando os polígonos sem necessidade de junção ou transformação no cliente.

---

## 4. Otimização com `/map/values`

Quando o usuário apenas troca de indicador ou ano sem alterar o nível ou território visualizado, as geometrias já estão em cache no navegador. O endpoint `/map/values` retorna exatamente os mesmos metadados, valores e classes da coropleta, mas **omite os polígonos GeoJSON**. Isso reduz a carga de rede de ~1 MB para meros ~15 KB.

---

## 5. CRUD de Visualizações Salvas (`/views`)

Permite ao usuário persistir e restaurar recortes favoritos do mapa (combinação de nível territorial, território pai, indicador, ano e número de classes).

### Validações na Escrita (`POST` e `PUT`)

O backend valida o recorte no momento de salvar:

- **Indicador:** Deve constar no catálogo ativo (`404 indicator_not_found` se inexistente).
- **Município sem pai:** `level=municipality` sem `parentCode` é rejeitado com `400 invalid_parameter`.
- **Território pai:** Deve existir e ser de nível hierarquicamente superior (`404` ou `400`).
- **Unicidade de nome:** Nomes duplicados (case-insensitive) retornam `409 saved_view_name_taken`.
- **Ano:** Deve estar no intervalo plausível 1900–2100 ou ser a string `"latest"`.

### Exemplo de Criação (`POST /api/v1/views`)

**Requisição:**
```http
POST /api/v1/views HTTP/1.1
Content-Type: application/json

{
  "name": "PIB per capita dos Estados (Recente)",
  "description": "Visão geral da disparidade de renda estadual",
  "level": "state",
  "parentCode": null,
  "indicatorKey": "gdp_per_capita",
  "year": "latest",
  "classes": 5
}
```

**Resposta (`201 Created`):**
```json
{
  "id": "21065c91-257a-4921-b661-239e88e75ef9",
  "name": "PIB per capita dos Estados (Recente)",
  "description": "Visão geral da disparidade de renda estadual",
  "level": "state",
  "parentCode": null,
  "indicatorKey": "gdp_per_capita",
  "year": "latest",
  "classes": 5,
  "createdAt": "2026-09-16T13:56:43.735Z",
  "updatedAt": "2026-09-16T13:56:43.735Z"
}
```

---

## 6. Municípios Seguidos (`/me/followed-municipalities`)

Permite ao usuário marcar municípios de interesse para acompanhamento rápido de clima e alertas.

- `GET /api/v1/me/followed-municipalities`: Retorna a lista dos municípios seguidos com nomes e UFs resolvidos.
- `PUT /api/v1/me/followed-municipalities/{ibge_code}`: Adiciona um município à lista (`201` para nova inserção ou `200` caso já seguisse).
- `DELETE /api/v1/me/followed-municipalities/{ibge_code}`: Remove o município da lista de forma idempotente (`204 No Content`).

*(Atualmente o endpoint opera com base em um identificador de sessão local; a estrutura está pronta para receber autenticação por token JWT sem necessidade de alteração de schema).*

