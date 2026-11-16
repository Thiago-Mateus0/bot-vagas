"""Greenhouse Job Board API (pública, sem chave).

GET https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true
Resposta: {"jobs": [{"id", "title", "absolute_url", "location": {"name"}, "content" (HTML
escapado), "updated_at", "offices": [...], ...}], "meta": {"total"}}
"""

from __future__ import annotations

import html
from datetime import datetime
from typing import Any

from . import Vaga
from .http import ClienteHTTP
from .texto import html_para_texto


class Greenhouse:
    nome = "greenhouse"

    def __init__(self, token: str):
        if not token or "/" in token:
            raise ValueError(f"token Greenhouse inválido: {token!r}")
        self.token = token

    @property
    def url(self) -> str:
        return f"https://boards-api.greenhouse.io/v1/boards/{self.token}/jobs?content=true"

    def buscar(self, http: ClienteHTTP) -> list[Vaga]:
        return converter(http.get_json(self.url), self.token)


def _data(valor: Any) -> datetime | None:
    if not isinstance(valor, str):
        return None
    try:
        return datetime.fromisoformat(valor.replace("Z", "+00:00"))
    except ValueError:
        return None


def converter(dados: Any, token: str) -> list[Vaga]:
    jobs = dados.get("jobs") if isinstance(dados, dict) else None
    if not isinstance(jobs, list):
        return []
    vagas: list[Vaga] = []
    for j in jobs:
        if not isinstance(j, dict) or "id" not in j or not j.get("title") or not j.get("absolute_url"):
            continue
        loc = (j.get("location") or {}).get("name") or ""
        conteudo = j.get("content") or ""
        # o Greenhouse devolve o HTML com entidades escapadas (&lt;p&gt;...)
        descricao = html_para_texto(html.unescape(conteudo))
        empresa = (j.get("company_name") or token).strip()
        remoto = None
        texto_loc = loc.lower()
        if "remote" in texto_loc or "remoto" in texto_loc:
            remoto = True
        vagas.append(
            Vaga(
                id_fonte=str(j["id"]),
                fonte="greenhouse",
                empresa=empresa,
                cargo=str(j["title"]).strip(),
                localizacao=loc.strip(),
                remoto=remoto,
                descricao=descricao,
                url=str(j["absolute_url"]),
                data=_data(j.get("updated_at") or j.get("first_published")),
            )
        )
    return vagas
