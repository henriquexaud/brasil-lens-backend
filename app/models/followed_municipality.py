"""Município seguido: a relação `usuário ↔ município` do contexto Clima.

É a base de um futuro sistema de alertas — "avise-me quando houver chuva forte
onde eu acompanho" —, mas por enquanto guarda **só a relação** e a preferência
de recebê-los (`notifications_enabled`). Nada de regra de disparo, canal de
entrega ou dado meteorológico aqui: essas coisas vão morar em tabelas próprias
que referenciam esta, quando existirem.

Duas decisões, pelos mesmos motivos das visualizações salvas:

* o município é guardado pelo **código IBGE**, sem FK para `territories` e sem
  copiar nome ou UF — nome e UF são lidos por JOIN na listagem. A existência
  e o nível do território são validados na escrita, no serviço;
* o usuário é um **identificador opaco** (`user_id`), não uma FK. Ainda não há
  autenticação: até lá a API grava todos os vínculos sob um usuário local (ver
  `app/api/deps.py::get_current_user_id`). Quando houver, o identificador do
  provedor de identidade entra no mesmo campo, sem migração de forma.
"""

from __future__ import annotations

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Identity,
    Index,
    Integer,
    String,
    UniqueConstraint,
    true,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class FollowedMunicipality(Base, TimestampMixin):
    __tablename__ = "followed_municipalities"

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)

    # Largo o bastante para um `sub` de OIDC ou um UUID em texto.
    user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    # Código IBGE de 7 dígitos — o mesmo `ibgeCode` do resto da API.
    municipality_code: Mapped[str] = mapped_column(String(7), nullable=False)
    # Ligadas por padrão ao seguir: é o gesto principal, desligar é a exceção.
    notifications_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=true()
    )

    __table_args__ = (
        # Seguir duas vezes o mesmo município não existe. Como a coluna líder é
        # `user_id`, o índice desta constraint também serve à listagem do usuário.
        UniqueConstraint("user_id", "municipality_code"),
        CheckConstraint("municipality_code ~ '^[0-9]{7}$'", name="municipality_code_format"),
        CheckConstraint("length(btrim(user_id)) > 0", name="user_id_not_blank"),
        # A pergunta inversa — "quem segue este município?" — é a que um
        # disparador de alertas fará. Barato agora, caro de esquecer depois.
        Index("ix_followed_municipalities_municipality_code", "municipality_code"),
    )

    def __repr__(self) -> str:  # pragma: no cover - diagnóstico
        return f"<FollowedMunicipality {self.user_id} → {self.municipality_code}>"
