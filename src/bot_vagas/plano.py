"""Plano de adaptação: o que o LLM pode mudar no currículo para uma vaga.

Só é permitido: reordenar competências, destacar bullets, reordenar projetos e
reescrever o resumo. Tudo referencia IDs do perfil; o verificador confere.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from .perfil import Idioma, Perfil


class PlanoAdaptacao(BaseModel):
    """Schema usado também como saída estruturada do modelo (por isso todos os campos
    são obrigatórios e `extra="forbid"` gera `additionalProperties: false`)."""

    model_config = ConfigDict(extra="forbid")

    idioma: Idioma = Field(description="Idioma do currículo: 'pt' ou 'en'.")
    resumo: str = Field(
        min_length=1,
        description="Texto do resumo ('Sobre mim'), reescrito usando SOMENTE fatos do perfil.",
    )
    ordem_competencias: list[str] = Field(
        description="IDs das tecnologias do perfil na ordem desejada. As não listadas vêm depois, na ordem original.",
    )
    bullets_destaque: list[str] = Field(
        description="IDs de bullets de experiência/educação a destacar (aparecem primeiro e em negrito).",
    )
    ordem_projetos: list[str] = Field(
        description="IDs dos projetos na ordem desejada. Os não listados vêm depois, na ordem original.",
    )


def plano_neutro(perfil: Perfil, idioma: Idioma) -> PlanoAdaptacao:
    """Plano que reproduz o currículo original sem nenhuma adaptação."""
    return PlanoAdaptacao(
        idioma=idioma,
        resumo=perfil.resumo.em(idioma),
        ordem_competencias=perfil.ids_tecnologias(),
        bullets_destaque=[],
        ordem_projetos=perfil.ids_projetos(),
    )


def ordenar_por_ids[T](itens: list[T], ordem: list[str], chave=lambda x: x.id) -> list[T]:
    """Coloca primeiro os itens cujos IDs estão em `ordem` (nessa ordem) e depois o resto."""
    por_id = {chave(i): i for i in itens}
    primeiros = [por_id[i] for i in ordem if i in por_id]
    vistos = set(ordem)
    return primeiros + [i for i in itens if chave(i) not in vistos]
