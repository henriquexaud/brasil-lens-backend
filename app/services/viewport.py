"""Validação compartilhada das consultas espaciais sob demanda."""

import math

from app.core.errors import InvalidParameterError


def parse_bbox(value: str, max_span: float = 360) -> tuple[float, float, float, float]:
    try:
        west, south, east, north = (float(part) for part in value.split(","))
        if not (
            all(math.isfinite(x) for x in (west, south, east, north))
            and -180 <= west < east <= 180
            and -90 <= south < north <= 90
            and east - west <= max_span
            and north - south <= max_span
        ):
            raise ValueError
        return west, south, east, north
    except ValueError as exc:
        raise InvalidParameterError(
            "Extensão geográfica inválida para esta escala.", "bbox"
        ) from exc
