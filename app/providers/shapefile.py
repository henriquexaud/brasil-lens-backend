"""Leitura mínima de shapefile (pontos e linhas) e DBF, com a biblioteca padrão.

As camadas de transporte do BC250 só usam os tipos Point (1) e PolyLine (3), em
graus. As coordenadas do .shp já são doubles little-endian na ordem x, y, a
mesma disposição do WKB, então cada parte vira WKB sem reconverter número a
número; o PostGIS recebe esse WKB direto.
"""

from __future__ import annotations

import struct
from collections.abc import Iterator, Sequence

from app.core.errors import ProviderError

NULL_SHAPE = 0
POINT = 1
POLYLINE = 3

_WKB_POINT = 1
_WKB_LINESTRING = 2
_WKB_MULTILINESTRING = 5


def wkb_point(x: float, y: float) -> bytes:
    return struct.pack("<BIdd", 1, _WKB_POINT, x, y)


def wkb_multilinestring(parts: Sequence[Sequence[Sequence[float]]]) -> bytes:
    chunks = [struct.pack("<BII", 1, _WKB_MULTILINESTRING, len(parts))]
    for part in parts:
        chunks.append(struct.pack("<BII", 1, _WKB_LINESTRING, len(part)))
        chunks.extend(struct.pack("<dd", point[0], point[1]) for point in part)
    return b"".join(chunks)


def _polyline_wkb(content: memoryview) -> bytes | None:
    num_parts, num_points = struct.unpack_from("<ii", content, 36)
    starts = struct.unpack_from(f"<{num_parts}i", content, 44)
    offset = 44 + 4 * num_parts
    chunks = [struct.pack("<BII", 1, _WKB_MULTILINESTRING, 0)]
    kept = 0
    for index, start in enumerate(starts):
        end = starts[index + 1] if index + 1 < num_parts else num_points
        if end - start < 2:
            continue  # parte degenerada: um ponto não é linha
        chunks.append(struct.pack("<BII", 1, _WKB_LINESTRING, end - start))
        chunks.append(bytes(content[offset + 16 * start : offset + 16 * end]))
        kept += 1
    if not kept:
        return None
    chunks[0] = struct.pack("<BII", 1, _WKB_MULTILINESTRING, kept)
    return b"".join(chunks)


def read_shapes(data: bytes) -> Iterator[bytes | None]:
    """Uma geometria WKB por registro, na ordem do arquivo (None para forma nula)."""
    if len(data) < 100 or struct.unpack_from(">i", data, 0)[0] != 9994:
        raise ProviderError("Arquivo .shp com cabeçalho inválido.")
    shape_type = struct.unpack_from("<i", data, 32)[0]
    if shape_type not in (POINT, POLYLINE):
        raise ProviderError(f"Tipo de shapefile não suportado: {shape_type}.")
    view = memoryview(data)
    offset = 100
    while offset + 8 <= len(data):
        length = struct.unpack_from(">i", data, offset + 4)[0] * 2
        content = view[offset + 8 : offset + 8 + length]
        offset += 8 + length
        record_type = struct.unpack_from("<i", content, 0)[0]
        if record_type == NULL_SHAPE:
            yield None
        elif record_type == POINT:
            x, y = struct.unpack_from("<dd", content, 4)
            yield wkb_point(x, y)
        elif record_type == POLYLINE:
            yield _polyline_wkb(content)
        else:
            raise ProviderError(f"Registro de shapefile com tipo inesperado: {record_type}.")


def read_dbf(data: bytes, encoding: str = "utf-8") -> Iterator[dict[str, str]]:
    """Os atributos de cada registro como texto, sem espaços nas pontas.

    Registros apagados (`*`) continuam na sequência, como dicionário vazio, para
    não desalinhar com as formas do .shp.
    """
    if len(data) < 32:
        raise ProviderError("Arquivo .dbf com cabeçalho inválido.")
    count, header_length, record_length = struct.unpack_from("<IHH", data, 4)
    fields: list[tuple[str, int]] = []
    position = 32
    while position < header_length - 1 and data[position] != 0x0D:
        name = data[position : position + 11].split(b"\0", 1)[0].decode("ascii").lower()
        fields.append((name, data[position + 16]))
        position += 32
    offset = header_length
    for _ in range(count):
        record = data[offset : offset + record_length]
        offset += record_length
        if record[:1] == b"*":
            yield {}
            continue
        values: dict[str, str] = {}
        column = 1
        for name, size in fields:
            text = record[column : column + size].decode(encoding, "replace").strip()
            # Numéricos nulos vêm preenchidos com asteriscos.
            values[name] = "" if text and set(text) == {"*"} else text
            column += size
        yield values
