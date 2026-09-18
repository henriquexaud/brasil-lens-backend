"""Cache TTL em processo para as projeções de leitura.

Justificativa: os dados mudam apenas durante a ingestão e a primeira requisição
de todo usuário é a mesma (`/map?level=state&indicator=...&year=latest`). Um dict
com TTL e limite de entradas resolve isso sem introduzir Redis. Quando existirem
múltiplas réplicas da API, cada uma mantém sua própria cópia — aceitável, porque
o conteúdo é idêntico e imutável entre ingestões.
"""

from __future__ import annotations

import time
from collections import OrderedDict
from typing import Any, Generic, TypeVar

T = TypeVar("T")


class TTLCache(Generic[T]):
    def __init__(self, ttl_seconds: int, max_entries: int) -> None:
        self._ttl = ttl_seconds
        self._max = max_entries
        self._data: OrderedDict[Any, tuple[float, T]] = OrderedDict()
        self.hits = 0
        self.misses = 0

    @property
    def enabled(self) -> bool:
        return self._ttl > 0 and self._max > 0

    def get(self, key: Any) -> T | None:
        if not self.enabled:
            return None
        entry = self._data.get(key)
        if entry is None:
            self.misses += 1
            return None
        expires_at, value = entry
        if expires_at < time.monotonic():
            del self._data[key]
            self.misses += 1
            return None
        self._data.move_to_end(key)
        self.hits += 1
        return value

    def set(self, key: Any, value: T, *, ttl_seconds: int | None = None) -> None:
        """Guarda um valor. `ttl_seconds` sobrepõe o TTL padrão.

        O TTL por entrada existe porque fontes diferentes têm cadências de
        atualização diferentes: dados censitários mudam a cada ingestão, mas
        uma camada meteorológica futura mudaria a cada hora.
        """
        if not self.enabled:
            return
        ttl = self._ttl if ttl_seconds is None else ttl_seconds
        if ttl <= 0:
            return
        self._data[key] = (time.monotonic() + ttl, value)
        self._data.move_to_end(key)
        while len(self._data) > self._max:
            self._data.popitem(last=False)

    def clear(self) -> None:
        self._data.clear()

    def stats(self) -> dict[str, int]:
        return {"entries": len(self._data), "hits": self.hits, "misses": self.misses}
