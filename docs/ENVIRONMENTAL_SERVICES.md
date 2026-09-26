# Serviços Ambientais e Meteorológicos — Brasil Lens

O Brasil Lens integra geografia territorial do IBGE com dados ambientais sobre clima, focos de calor, alertas de desastres naturais e hidrografia.

---

## 1. Visão Geral dos Provedores

| Provedor | Dados Fornecidos | Protocolo / Origem | Cache / Estratégia |
|---|---|---|---|
| **Open-Meteo** | Condições atuais e previsão de 3 dias (temperatura, chuva, vento, umidade) | API REST JSON aberta | Cache em Redis (15–30 min), fallback local |
| **INMET** | Avisos de eventos meteorológicos severos (chuva forte, onda de calor, vendaval) | API REST JSON | Ingestão periódica com categorização normalizada |
| **CEMADEN** | Alertas de riscos geo-hidrológicos (alagamentos, enxurradas, deslizamentos) | GeoServer OGC WFS (`alertas_vigentes_siaden`) | Validade estendida por buffer de 4h |
| **INPE (BDQueimadas)** | Detecções de focos de calor e potência radiativa de fogo (FRP) | GeoServer OGC WFS / WMS (`bdqueimadas:focos`) | WMS em zoom próximo, agregação e densidade por 1.000 km² em zoom amplo |
| **ANA** | Malha de hidrografia (rios e corpos d'água) | API REST GeoJSON | Simplificação conforme nível de zoom |

---

## 2. Clima e Chuva (Open-Meteo)

- **Condições Atuais nas Capitais (`GET /weather/current`):** Consulta inicial agregada de todas as 27 capitais brasileiras, servindo como referência instantânea para colorir os estados na tela inicial.
- **Média Ponderada por Estado (`GET /weather/states`):** Para evitar que o clima de uma UF seja representado apenas pela sua capital, o sistema calcula uma média espacial ponderada por polígonos de Voronoi/Thiessen baseados na malha territorial canônica do PostGIS.
- **Clima por Área Visível (`GET /weather/viewport`):** No nível de aproximação municipal, os municípios visíveis são agregados em células de grade espacial (0,5° a 0,25°). A API consulta municípios estratégicos e interpola as estimativas dos vizinhos, reduzindo drasticamente as chamadas externas.

---

## 3. Focos de Calor (INPE / BDQueimadas)

Os focos de queimadas são obtidos diretamente dos serviços públicos do Instituto Nacional de Pesquisas Espaciais (INPE):

- **Agregação e Densidade (`GET /fire-hotspots/summary`):** Em níveis nacional e estadual, o sistema calcula a densidade de focos por 1.000 km²:
  $$\text{Densidade} = \frac{\text{Contagem de Focos em 48h} \times 1000}{\text{Área Canônica em km²}}$$
  A área utilizada é calculada de forma geodésica no PostGIS com a malha oficial do IBGE.
- **Visualização Dinâmica (WMS):** Em níveis de zoom mais próximos (zoom $\ge$ 9), o frontend conecta diretamente com a camada WMS do GeoServer do INPE para exibir as detecções pontuais exatas.
- **Identificação Pontual (`GET /fire-hotspots/identify`):** Ao clicar em um foco no mapa, a API busca as detecções em um raio de tolerância espacial, retornando satélite sensor, FRP (Fire Radiative Power em MW) e horário da passagem.

---

## 4. Alertas de Desastres Naturais (`GET /weather/alerts`)

Integra duas fontes oficiais complementares:
1. **INMET:** Foco no fenômeno meteorológico de origem.
2. **CEMADEN:** Foco no risco geo-hidrológico de impacto (deslizamento, enxurrada).

O backend normaliza ambas as fontes para um modelo comum de severidade (`potential`, `danger`, `extreme`), permitindo que a interface web apresente os riscos de forma clara e prioritária.

---

## 5. Arquitetura de Cache com Redis

O backend utiliza uma instância interna do **Redis 7.4** (`redis:7.4-alpine`) orquestrada pelo Docker Compose para garantir tempos de resposta de milissegundos e proteger os limites de requisições de provedores abertos:

- **Leituras Meteorológicas:** Chaves expiradas por horário da própria leitura (15 a 30 minutos). Em caso de queda momentânea do provedor externo, a última leitura é servida como `stale` por até 12 horas.
- **Metadados de Queimadas (INPE):** Cache compartilhado com TTL de 10 minutos com deduplicação de requisições concorrentes.
- **Geometrias e Resumos:** Cache persistido com política `allkeys-lru` e limite de memória de 256 MB.
