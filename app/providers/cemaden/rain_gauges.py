"""Pluviômetros automáticos do CEMADEN.

**Sem endpoint público confirmado.** Diferente do INMET (`/estacoes/T` e
`/avisos/ativos` confirmados por chamada HTTP real nesta sessão), o CEMADEN
não expôs um contrato JSON verificável pelas ferramentas desta sessão:

- `mapainterativo.cemaden.gov.br` é uma SPA — o HTML servido não referencia a
  URL da API que a página de fato consulta (o carregamento acontece via JS
  depois da carga inicial, e a ferramenta de busca usada aqui não executa
  JavaScript nem inspeciona chamadas de rede).
- Existem clientes de terceiros de código aberto que já consomem dados do
  CEMADEN (ex.: `github.com/rafa-rebelo/ews-dados-pluviometricos`), o que
  sugere que um endpoint estável existe — mas ele precisa ser descoberto
  inspecionando as chamadas de rede do mapa interativo num navegador de
  verdade (DevTools → Network), o que está fora do alcance das ferramentas
  usadas para desenhar este provider.

**Este módulo fica com a interface pronta e deliberadamente inerte** — o
mesmo formato de `app/providers/inmet/stations.py` — para o dia em que o
endpoint for confirmado, sem exigir mudança de contrato em nenhuma camada
acima (job, repositório, API). Até lá, `fetch_stations`/`fetch_latest_reading`
levantam `ProviderError`, o job registra a falha em `ingestion_runs` como
`failed` sem derrubar o processo, e `GET /weather/sources` mostra
`cemaden_rain_gauges` como indisponível — nunca substituído por uma API
comercial só para "funcionar", o que contrariaria o pedido de priorizar
fontes oficiais.
"""

from __future__ import annotations

import httpx

from app.core.errors import ProviderError
from app.providers.records import WeatherObservationRecord, WeatherStationRecord

SOURCE = "cemaden_pluviometros"
PROVIDER_KEY = "cemaden"

_UNAVAILABLE_MESSAGE = (
    "Endpoint público do CEMADEN ainda não confirmado — ver docstring do módulo "
    "app/providers/cemaden/rain_gauges.py para o que já foi investigado."
)


async def fetch_stations(client: httpx.AsyncClient) -> list[WeatherStationRecord]:
    raise ProviderError(_UNAVAILABLE_MESSAGE)


async def fetch_latest_reading(
    client: httpx.AsyncClient, station_code: str
) -> WeatherObservationRecord | None:
    raise ProviderError(_UNAVAILABLE_MESSAGE)
