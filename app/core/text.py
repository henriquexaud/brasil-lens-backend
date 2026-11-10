import re
import unicodedata


def normalize_text(text: str) -> str:
    decomposed = unicodedata.normalize("NFD", text)
    return "".join(c for c in decomposed if unicodedata.category(c) != "Mn").lower().strip()


_LOWERCASE_WORDS = frozenset(
    {"a", "à", "as", "da", "das", "de", "do", "dos", "e", "em", "na", "no"}
)
_KEPT_UPPERCASE = frozenset(
    {"S.A.", "S/A", "SA", "LTDA", "ME", "EPP", "EIRELI", "II", "III", "IV", "APA", "ARIE", "RPPN"}
)


def title_case(text: str) -> str:
    """'ÁREA DE PROTEÇÃO DA SERRA' → 'Área de Proteção da Serra' (fontes em maiúsculas)."""
    words = text.split()
    result = []
    for index, word in enumerate(words):
        lower = word.lower()
        if word.upper() in _KEPT_UPPERCASE:
            result.append(word.upper())
        elif index > 0 and lower in _LOWERCASE_WORDS:
            result.append(lower)
        else:
            result.append(
                re.sub(r"(^|[-/(])(\w)", lambda m: m.group(1) + m.group(2).upper(), lower)
            )
    return " ".join(result)
