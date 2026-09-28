# Clima (Open-Meteo)

A Open-Meteo é aberta e sem chave, com cota de ~10 mil chamadas/dia por IP. Ela fornece as condições atuais, a previsão de 3 dias e a chuva horária das últimas 48 h. Código: `providers/open_meteo.py`, `services/weather_forecast.py` e `services/spatial_interpolation.py`. Testes: `test_weather_forecast.py`.

## O que é medido em cada escala

| Rota | O que é medido | O que é estimado |
|---|---|---|
| `/weather/current` | As 27 capitais, numa chamada só | — |
| `/weather/current?territory=` | UF: a capital. Município: o próprio ponto | — |
| `/weather/states` | De 2 a 8 pontos por UF (1 a cada 60 mil km²) | A UF é a média ponderada pela área dos municípios mais próximos de cada ponto |
| `/weather/state?parent=` | Os 20 primeiros pontos da amostra da UF | IDW com os 3 vizinhos mais próximos |
| `/weather/viewport` | 1 ponto por célula de grade (1° a 0,25°, ou todos em zoom próximo), no máximo 80 | Interpolação a partir de tudo que já é conhecido |
| `/weather/municipalities` | Cada município da página | — |

Na média estadual, o céu é o código de maior peso, e a chuva usa o máximo ou "algum ponto está chovendo", para não diluir chuva local. `samplePoints` e `rainingPoints` mostram a cobertura.

## Leitura, frescor e fallback

- Cada ponto tem uma leitura (`current` ou `forecast`, e esta também grava `current`), guardada em memória e no Redis (`weather-reading-v2`) por 12 h.
- A leitura é **fresca** até `observedAt` + 15 min (território selecionado) ou + 30 min (mapa), e nunca é buscada de novo antes de 2 min. **Vencida mas usável** (até 2 h): volta na hora e é renovada em segundo plano. **Mais velha que isso**: é buscada de novo. Se a busca falha, a leitura de até 12 h é servida como `stale`.
- As requisições vão em lotes de até 100 locais, compartilhadas por ponto enquanto estão em voo (`_inflight` + `asyncio.shield`). A resposta de `/weather/state` fica 60 s em cache.
- Normalização (`_parse_city`): °C, km/h, mm e UTC. `precipitation48hMm` soma as horas com valor. `rainingNow` é verdadeiro se chove no intervalo atual ou se o código indica chuva. Resposta incompleta vira `ProviderError`.

## Falhas

- **429:** vira `provider_rate_limited`, com espera igual ao `Retry-After` ou à janela da cota: 60 s (minuto), 300 s (hora) ou 900 s (dia). Enquanto dura, nada sai.
- **Outros erros:** cooldown de 60 s.
- Em desenvolvimento a cota acaba rápido. Não faça loops contra a fonte; no navegador, intercepte o fetch.
