"""Carrega fontes.yaml e instancia as fontes."""

from __future__ import annotations

from pathlib import Path

import yaml

from ..perfil import RAIZ_PROJETO
from . import Fonte
from .greenhouse import Greenhouse
from .gupy import Gupy
from .lever import Lever

FONTES_PADRAO = RAIZ_PROJETO / "fontes.yaml"


def carregar_fontes(caminho: Path | str = FONTES_PADRAO) -> list[Fonte]:
    with open(caminho, encoding="utf-8") as f:
        dados = yaml.safe_load(f) or {}
    if not isinstance(dados, dict):
        raise ValueError(f"fontes.yaml inválido em {caminho}")
    fontes: list[Fonte] = []
    for item in dados.get("greenhouse") or []:
        fontes.append(Greenhouse(str(item["token"]) if isinstance(item, dict) else str(item)))
    for item in dados.get("lever") or []:
        fontes.append(Lever(str(item["empresa"]) if isinstance(item, dict) else str(item)))
    gupy = dados.get("gupy")
    if isinstance(gupy, dict):
        termos = [str(t) for t in (gupy.get("termos") or [])]
        if termos:
            fontes.append(Gupy(
                termos,
                max_paginas_por_termo=int(gupy.get("max_paginas_por_termo", 5)),
                max_requisicoes=int(gupy.get("max_requisicoes", 40)),
                pausa_segundos=float(gupy.get("pausa_segundos", 2)),
            ))
    elif isinstance(gupy, list) and gupy:  # formato antigo: lista de termos / {termo: ...}
        termos = [str(i.get("termo", "")) if isinstance(i, dict) else str(i) for i in gupy]
        termos = [t for t in termos if t.strip()]
        if termos:
            fontes.append(Gupy(termos))
    return fontes
