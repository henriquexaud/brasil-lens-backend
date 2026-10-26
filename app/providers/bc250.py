"""Malha de transporte da Base Cartográfica Contínua 1:250.000 do IBGE (BC250).

O zip oficial tem ~800 MB, mas o servidor aceita HTTP Range: o `zipfile` lê o
diretório central e só as camadas usadas (~50 MB comprimidos). Rodovias
federais vêm do SNV do DNIT (`providers/dnit.py`); daqui saem as estaduais,
as locais, as ferrovias, as hidrovias e os portos.
"""

from __future__ import annotations

import io
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from app.core.errors import ProviderError
from app.providers.shapefile import read_dbf, read_shapes

SOURCE = "IBGE"
EDITION = "2025"
DATASET_URL = (
    "https://www.ibge.gov.br/geociencias/cartas-e-mapas/bases-cartograficas-continuas/"
    "15759-brasil.html"
)
DOWNLOAD_URL = (
    "https://geoftp.ibge.gov.br/cartas_e_mapas/bases_cartograficas_continuas/bc250/"
    "versao2025/shapefiles/bc_250_shapefiles_2026_03_03.zip"
)

ROADS = "rod_trecho_rodoviario_l"
RAILWAYS = "fer_trecho_ferroviario_l"
WATERWAYS = "hdv_trecho_hidroviario_l"
PORTS = "hdv_complexo_portuario_p"
MEMBERS = (ROADS, RAILWAYS, WATERWAYS, PORTS)

# Só se desenha o que existe: planejado, em construção, abandonado ou destruído fica de fora.
_EXISTING = frozenset({"Construída", "Construída, mas em obras", "Desconhecida", ""})
_UNKNOWN = frozenset({"", "Desconhecida", "Desconhecido", "Não aplicável", "Outros"})


@dataclass(frozen=True, slots=True)
class TransportFeature:
    layer: str
    name: str | None
    geometry: bytes
    attributes: dict[str, str] = field(default_factory=dict)


class _RemoteFile(io.RawIOBase):
    """Arquivo remoto somente leitura, buscado por faixas de bytes."""

    def __init__(self, client: httpx.Client, url: str) -> None:
        self._client = client
        self._url = url
        self._position = 0
        response = client.head(url)
        if response.status_code != httpx.codes.OK or "content-length" not in response.headers:
            raise ProviderError(
                f"IBGE respondeu HTTP {response.status_code} ao consultar o BC250.", url=url
            )
        self._size = int(response.headers["content-length"])

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self._position

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        base = {io.SEEK_SET: 0, io.SEEK_CUR: self._position, io.SEEK_END: self._size}[whence]
        self._position = base + offset
        return self._position

    def readinto(self, buffer: bytearray | memoryview) -> int:  # type: ignore[override]
        if self._position >= self._size:
            return 0
        end = min(self._position + len(buffer), self._size) - 1
        response = self._client.get(self._url, headers={"Range": f"bytes={self._position}-{end}"})
        if response.status_code != httpx.codes.PARTIAL_CONTENT:
            raise ProviderError(
                f"IBGE não atendeu a leitura parcial do BC250 (HTTP {response.status_code}).",
                url=self._url,
            )
        data = response.content
        buffer[: len(data)] = data
        self._position += len(data)
        return len(data)


def _read_layers(archive: zipfile.ZipFile) -> dict[str, tuple[bytes, bytes, str]]:
    names = {Path(name).name: name for name in archive.namelist()}
    layers: dict[str, tuple[bytes, bytes, str]] = {}
    for member in MEMBERS:
        try:
            shp = archive.read(names[f"{member}.shp"])
            dbf = archive.read(names[f"{member}.dbf"])
        except KeyError as exc:
            raise ProviderError(f"O BC250 não tem a camada {member}.") from exc
        cpg = names.get(f"{member}.cpg")
        encoding = archive.read(cpg).decode("ascii").strip() if cpg else "latin-1"
        layers[member] = (shp, dbf, encoding)
    return layers


def download_layers(file: Path | None = None) -> dict[str, tuple[bytes, bytes, str]]:
    """Lê `.shp`, `.dbf` e codificação das camadas usadas, do zip local ou remoto.

    Síncrono (o `zipfile` exige um arquivo com `seek`): chame com `asyncio.to_thread`.
    """
    if file is not None:
        with zipfile.ZipFile(file) as archive:
            return _read_layers(archive)
    try:
        with httpx.Client(timeout=120, follow_redirects=True) as client:
            remote = io.BufferedReader(_RemoteFile(client, DOWNLOAD_URL), buffer_size=1 << 20)
            with zipfile.ZipFile(remote) as archive:
                return _read_layers(archive)
    except (httpx.HTTPError, zipfile.BadZipFile) as exc:
        raise ProviderError(f"Falha ao ler o BC250 do IBGE: {exc}", url=DOWNLOAD_URL) from exc


def _known(value: str) -> str | None:
    return None if value in _UNKNOWN else value


def _attributes(**values: str) -> dict[str, str]:
    return {key: known for key, value in values.items() if (known := _known(value))}


def _records(layer: tuple[bytes, bytes, str]) -> Iterator[tuple[bytes, dict[str, str]]]:
    shp, dbf, encoding = layer
    shapes = list(read_shapes(shp))
    rows = list(read_dbf(dbf, encoding))
    if len(shapes) != len(rows):
        raise ProviderError("O .shp e o .dbf do BC250 têm quantidades diferentes de registros.")
    for geometry, row in zip(shapes, rows, strict=True):
        if geometry is not None and row:
            yield geometry, row


def roads(layer: tuple[bytes, bytes, str]) -> Iterator[TransportFeature]:
    """Estaduais e locais. As federais do BC250 ficam de fora: vêm do SNV do DNIT."""
    for geometry, row in _records(layer):
        jurisdiction = row.get("jurisdicao", "")
        if jurisdiction in ("Federal", "Propriedade particular"):
            continue
        if row.get("situacaofi", "") not in _EXISTING:
            continue
        state = jurisdiction == "Estadual/Distrital"
        # Nas ~74 mil locais, administração e concessão quase nunca são conhecidas:
        # guardar só o revestimento poupa espaço no banco.
        attributes = (
            _attributes(
                surface=row.get("revestimen", ""),
                administration=row.get("administra", ""),
                concession=row.get("concession", ""),
            )
            if state
            else _attributes(surface=row.get("revestimen", ""))
        )
        yield TransportFeature(
            layer="road_state" if state else "road_local",
            name=row.get("sigla") or None,
            geometry=geometry,
            attributes=attributes,
        )


def railways(layer: tuple[bytes, bytes, str]) -> Iterator[TransportFeature]:
    for geometry, row in _records(layer):
        if row.get("situacaofi", "") not in _EXISTING:
            continue
        yield TransportFeature(
            layer="rail",
            name=row.get("nome") or None,
            geometry=geometry,
            attributes=_attributes(
                gauge=row.get("bitola", ""),
                electrified=row.get("eletrifica", ""),
                operator=row.get("concession", "") or row.get("administra", ""),
                operating=row.get("operaciona", ""),
            ),
        )


def waterways(layer: tuple[bytes, bytes, str]) -> Iterator[TransportFeature]:
    for geometry, row in _records(layer):
        if row.get("situacaofi", "") not in _EXISTING:
            continue
        yield TransportFeature(
            layer="waterway",
            name=row.get("nome") or None,
            geometry=geometry,
            attributes=_attributes(
                regime=row.get("regime", ""), operating=row.get("operaciona", "")
            ),
        )


def ports(layer: tuple[bytes, bytes, str]) -> Iterator[TransportFeature]:
    for geometry, row in _records(layer):
        if row.get("situacaofi", "") not in _EXISTING or row.get("operaciona") == "Não":
            continue
        yield TransportFeature(
            layer="port",
            name=row.get("nome") or None,
            geometry=geometry,
            attributes=_attributes(
                cargo=row.get("tipotransp", ""), administration=row.get("administra", "")
            ),
        )


def features(layers: dict[str, tuple[bytes, bytes, str]]) -> Iterator[TransportFeature]:
    yield from roads(layers[ROADS])
    yield from railways(layers[RAILWAYS])
    yield from waterways(layers[WATERWAYS])
    yield from ports(layers[PORTS])
