"""Alertas de risco geo-hidrológico do CEMADEN.

Contrato confirmado por chamada HTTP real ao GeoServer que também alimenta o
Mapa Interativo público (https://mapainterativo.cemaden.gov.br) — o mesmo
processo de descoberta usado para o INMET (ver `app/providers/inmet/alerts.py`):
nenhuma das duas fontes documenta um contrato de API formal, então o contrato
real só se confirma rodando contra a fonte. Aqui, a busca partiu do JavaScript
do próprio mapa (`script.js`), que expõe:

    var geoserverPath = "https://gsc.cemaden.gov.br/geoserver/cemaden_dev/wms";

O `GetCapabilities` do mesmo GeoServer (troque `wms` por `ows`) lista a camada
`alertas_vigentes_siaden` — exatamente os alertas em vigor exibidos no mapa.
Diferente do WMS (pixels), o WFS devolve GeoJSON com atributos, é protocolo
OGC padrão e autodescritivo (`DescribeFeatureType`), e é a mesma técnica já
usada neste projeto para o INPE/BDQueimadas (`app/services/fire_hotspots.py`)
— por isso preferido a um endpoint JSON ad-hoc, mesmo sem documentação de
negócio: o risco de quebra é o de um schema OGC mudar, não o de um endpoint
interno desaparecer sem aviso.

Exemplo real (`GetFeature`, `outputFormat=application/json`):

```json
{
  "id_alerta": 35454, "cod_alerta": 2732,
  "datahoracriacao": "2026-09-20T08:07:29.832Z",
  "ult_atualizacao": "2026-09-20T08:07:29.832Z",
  "vigencia": "2026-09-20T08:07:29.832Z",
  "cidade": "BLUMENAU", "uf": "SC", "descricao": "Blumenau",
  "evento": "Movimentos de Massa - Moderado", "nivel": "Moderado",
  "status": 1,
  "path_pdf": "https://siaden.cemaden.gov.br/dados/resources/FilePDF/....pdf",
  "codibge": 4202404
}
```

Decisões que só ficaram claras com o exemplo real:

- **Não há campo de expiração, e o candidato óbvio não é confiável.** Ao
  contrário do INMET (`data_fim` explícito), o CEMADEN só publica
  `vigencia`/`ult_atualizacao` — em tese, "última vez que a fonte confirmou
  este alerta como ativo". Medido de verdade (rodando o job contra a fonte
  real, horas depois de uma consulta anterior): vários alertas ainda
  `status=1` tinham `vigencia` de mais de 8 h atrás, sem nenhuma atualização
  visível nesse intervalo — não dá para assumir um heartbeat frequente da
  fonte. Por isso `expires` não vem de `vigencia`: vem do nosso próprio ciclo
  de ingestão — `expires = <quando este job rodou> +
  settings.cemaden_alert_validity_buffer_seconds`. Enquanto o alerta
  continuar aparecendo no `GetFeature` a cada ciclo do scheduler, a expiração
  sempre reabre; se sumir do feed (ou virar "Cessar"), some do mapa em até um
  buffer — mesmo raciocínio de `_STALE_MULTIPLIER` em `services/weather.py`,
  só que aplicado por alerta em vez de por fonte inteira.
- **`status=0`/`nivel="Cessar"` é o próprio alerta anunciando o fim**, não um
  estado transitório — a legenda oficial do GeoServer confirma isso com uma
  regra dedicada (cinza `#4F4F4F`, só para "Cessar"). Mesmo papel do
  `encerrado` do INMET: filtrado no próprio GeoServer (`cql_filter=status=1`)
  e nunca vira linha na tabela.
- **`codibge` é sempre um único município.** Diferente do INMET, que pode
  cobrir estados inteiros sem listar município nenhum, o CEMADEN opera no
  recorte municipal — a rede de monitoramento e a Defesa Civil que recebe o
  alerta são locais.
- **`evento` mistura tipo e nível** (`"Movimentos de Massa - Moderado"`): o
  nível já vem em campo próprio (`nivel`), então `event` fica só com o tipo
  (o sufixo `" - {nivel}"` é removido), evitando mostrar "Moderado" duas vezes
  no mesmo alerta.
- **Não há texto estruturado de risco/instrução nesta camada** — só um link
  para o boletim em PDF (`path_pdf`). Guardamos o link em `instructions` em
  vez de inventar texto que a fonte não deu (ver `_pdf_instruction`).
- **Cor oficial vem da legenda do WMS** (`GetLegendGraphic`), não de um campo
  do registro: `#FFFF00`/`#FFA500`/`#FF0000` para Moderado/Alto/Muito Alto —
  mesmo espírito do `aviso_cor` do INMET (cor da própria fonte, nunca da
  nossa paleta), só que aqui fixada em código por não vir no payload.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from app.core.config import settings
from app.core.errors import ProviderError
from app.providers.base import get_json
from app.providers.records import WeatherAlertRecord

SOURCE = "cemaden_alertas"
PROVIDER_KEY = "cemaden"
TYPE_NAME = "cemaden_dev:alertas_vigentes_siaden"

# Cor oficial por `nivel`, lida do GetLegendGraphic do próprio GeoServer — não
# está em nenhum atributo do registro (ao contrário do INMET, que manda
# `aviso_cor`). "Cessar" não entra: já é filtrado antes de chegar aqui.
_LEVEL_COLORS: dict[str, str] = {
    "Moderado": "#FFFF00",
    "Alto": "#FFA500",
    "Muito Alto": "#FF0000",
}

# Só os atributos que o parser usa — o mesmo raciocínio de `_FIELDS` em
# app/services/fire_hotspots.py: o GeoServer aceita `propertyName` e evita
# transferir (e descartar) o resto do payload. `the_geom` **precisa** estar
# na lista — confirmado rodando contra a fonte real: com `propertyName`
# restrito e sem `the_geom`, o WFS devolve `"geometry": null` em toda feature
# em vez da geometria completa (comportamento padrão de WFS: a lista
# restringe tudo, geometria inclusive, não só os atributos não-espaciais).
# `vigencia`/`ult_atualizacao` não entram: não usados (ver docstring do
# módulo sobre por que `expires` não é derivado deles).
_PROPERTIES = "id_alerta,datahoracriacao,cidade,uf,evento,nivel,status,path_pdf,codibge,the_geom"


async def fetch_active_alerts(client: httpx.AsyncClient) -> list[WeatherAlertRecord]:
    """Um único `GetFeature` já devolve todos os alertas em vigor — sem N+1
    por alerta ou por município, mesmo espírito de
    `inmet.alerts.fetch_active_alerts`. `status=1` filtra "Cessar" no próprio
    GeoServer (CQL), evitando transferir linhas que nunca virariam registro.
    """
    raw = await get_json(
        client,
        "/ows",
        params={
            "service": "WFS",
            "version": "2.0.0",
            "request": "GetFeature",
            "typeName": TYPE_NAME,
            "outputFormat": "application/json",
            "srsName": "EPSG:4326",
            "propertyName": _PROPERTIES,
            "cql_filter": "status=1",
        },
        source=SOURCE,
    )
    if not isinstance(raw, dict) or not isinstance(raw.get("features"), list):
        raise ProviderError(f"{SOURCE} devolveu formato inesperado para GetFeature.")

    # Um único carimbo para toda a leitura: `expires` marca quando *nós*
    # confirmamos o alerta, não quando a fonte diz tê-lo confirmado por
    # último — ver docstring do módulo sobre por que `vigencia` não serve
    # para isto.
    fetched_at = datetime.now(UTC)
    records: list[WeatherAlertRecord] = []
    for feature in raw["features"]:
        if not isinstance(feature, dict):
            continue
        record = _to_alert(feature, fetched_at)
        if record is not None:
            records.append(record)
    return records


def _to_alert(feature: dict[str, Any], fetched_at: datetime) -> WeatherAlertRecord | None:
    props = feature.get("properties")
    geometry = feature.get("geometry")
    if not isinstance(props, dict) or not isinstance(geometry, dict):
        return None
    # `cql_filter=status=1` já deveria garantir isto no próprio GeoServer, mas
    # não confiamos só no filtro do servidor — mesma cautela que o INMET tem
    # ao checar `encerrado` em Python mesmo consultando "/avisos/ativos".
    if props.get("status") != 1:
        return None

    external_id = props.get("id_alerta")
    onset = _parse_iso(props.get("datahoracriacao"))
    nivel = props.get("nivel")
    event = _event_type(props.get("evento"), nivel)
    if external_id is None or onset is None or event is None:
        return None

    # Ver docstring do módulo: expira a partir do nosso ciclo de ingestão, não
    # de `vigencia` — enquanto o alerta seguir no feed a cada ciclo, a
    # expiração sempre reabre.
    expires = fetched_at + timedelta(seconds=settings.cemaden_alert_validity_buffer_seconds)

    return WeatherAlertRecord(
        provider=PROVIDER_KEY,
        external_id=str(external_id),
        event=event,
        severity=nivel if isinstance(nivel, str) and nivel else "Desconhecida",
        onset=onset,
        expires=expires,
        polygon_geojson=geometry,
        color=_LEVEL_COLORS.get(nivel) if isinstance(nivel, str) else None,
        description=_description(props.get("cidade"), props.get("uf")),
        affected_ibge_codes=_ibge_code(props.get("codibge")),
        instructions=_pdf_instruction(props.get("path_pdf")),
    )


def _event_type(evento: Any, nivel: Any) -> str | None:
    """`evento` chega como `"<tipo> - <nivel>"`; `nivel` já vem em campo
    próprio, então o sufixo é redundante — sem ele, o tipo sozinho
    ("Movimentos de Massa", "Risco Hidrológico", "Inundação", "Enxurrada"...,
    conforme a legenda oficial da camada) é o que sobra para `event`.
    """
    if not isinstance(evento, str) or not evento.strip():
        return None
    cleaned = evento.strip()
    if isinstance(nivel, str) and nivel:
        suffix = f" - {nivel}"
        if cleaned.endswith(suffix):
            cleaned = cleaned[: -len(suffix)]
    return cleaned.strip() or None


def _description(cidade: Any, uf: Any) -> str | None:
    """Verbatim da fonte (mesmo raciocínio do `color`: não é nosso papel
    corrigir capitalização de topônimo). Só o CEMADEN preenche isto — o INMET
    não tem essa frase, e o default `None` de `WeatherAlertRecord` já cobre.
    """
    if not isinstance(cidade, str) or not cidade.strip():
        return None
    city = cidade.strip()
    return f"{city}/{uf.strip()}" if isinstance(uf, str) and uf.strip() else city


def _ibge_code(value: Any) -> tuple[str, ...]:
    if isinstance(value, bool):
        return ()
    if isinstance(value, int):
        return (str(value),)
    if isinstance(value, str) and value.strip().isdigit():
        return (value.strip(),)
    return ()


def _pdf_instruction(path_pdf: Any) -> tuple[str, ...]:
    if not isinstance(path_pdf, str) or not path_pdf.strip():
        return ()
    return (f"Boletim oficial do CEMADEN: {path_pdf.strip()}",)


def _parse_iso(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
