"""O tipo que um provider usa para se anunciar ao registro.

Módulo separado de `providers/registry.py` de propósito: o registro agrega o
descritor de cada provider (e por isso importa os pacotes de providers), e
cada provider precisa do tipo `ProviderDescriptor` para se descrever. Se os
dois vivessem no mesmo arquivo, `providers/ibge/__init__.py` importaria de
`providers/registry.py` e `providers/registry.py` importaria de volta
`providers/ibge` — um ciclo. Este módulo não importa nenhum provider, então
não existe ciclo a evitar.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.models import DataContext


@dataclass(frozen=True, slots=True)
class ProviderDescriptor:
    """Um provider e o que ele entrega — o bastante para descoberta.

    Não é um plugin nem uma interface a implementar: é só um registro
    declarativo, lido por `providers/registry.py`.
    """

    # Identificador estável do provider (ex.: "ibge"). Não é o `source` salvo
    # em `datasets.source` por coincidência de valor hoje — são dois conceitos
    # que hoje colidem porque só existe um provider; nada os obriga a colidir.
    key: str
    name: str
    context: DataContext
    # Chaves de indicador (`indicators.key`) que este provider consegue
    # fornecer — sourced e derivados. É o que a rota `/contexts` soma para
    # dizer "este contexto tem N indicadores disponíveis".
    provides: tuple[str, ...]
    homepage: str | None = None
