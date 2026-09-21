"""Normalização de texto compartilhada entre a ingestão e a busca."""

import unicodedata


def normalize_text(text: str) -> str:
    """Minúsculo e sem diacríticos: "São Paulo" e "sao paulo" viram o mesmo texto.

    A ingestão grava `normalized_name` nesta forma e a busca normaliza o termo
    digitado com esta mesma função — se as duas pontas divergirem, a busca
    deixa de casar sem nenhum erro aparente.
    """
    decomposed = unicodedata.normalize("NFD", text)
    return "".join(c for c in decomposed if unicodedata.category(c) != "Mn").lower().strip()
