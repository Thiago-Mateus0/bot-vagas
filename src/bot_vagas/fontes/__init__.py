"""Fontes de vagas plugáveis.

Cada fonte implementa `Fonte.buscar()` e devolve uma lista de `Vaga`. Nenhuma fonte faz rede
sozinha: todas recebem um `ClienteHTTP` injetável (o real só é usado por `bot-vagas buscar`).
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from .http import ClienteHTTP


class Vaga(BaseModel):
    """Vaga normalizada, independente da fonte."""

    model_config = ConfigDict(frozen=True)

    id_fonte: str = Field(min_length=1, description="ID da vaga na fonte de origem")
    fonte: str = Field(min_length=1, description="greenhouse | lever | gupy | velrino")
    empresa: str = Field(min_length=1)
    cargo: str = Field(min_length=1)
    localizacao: str = ""
    remoto: bool | None = Field(default=None, description="True/False se a fonte informa; None se desconhecido")
    descricao: str = ""
    url: str = Field(min_length=1)
    data: datetime | None = None


class Fonte(Protocol):
    nome: str

    def buscar(self, http: ClienteHTTP) -> list[Vaga]: ...


__all__ = ["Vaga", "Fonte", "ClienteHTTP"]
