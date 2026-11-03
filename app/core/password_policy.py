from functools import lru_cache
from pathlib import Path

# As ~10 mil senhas mais comuns (SecLists, Pwdb_top-10000), reduzidas às de 8+ caracteres
# (o cadastro já exige 8) em minúsculas. Barra a escolha preguiçosa sem depender de biblioteca.
_COMMON_PASSWORDS_FILE = Path(__file__).with_name("common_passwords.txt")


@lru_cache(maxsize=1)
def _common_passwords() -> frozenset[str]:
    return frozenset(_COMMON_PASSWORDS_FILE.read_text(encoding="utf-8").split("\n")) - {""}


def is_common_password(password: str) -> bool:
    return password.lower() in _common_passwords()
