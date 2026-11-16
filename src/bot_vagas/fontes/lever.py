"""Lever Postings API (pública, sem chave).

GET https://api.lever.co/v0/postings/{empresa}?mode=json
Resposta: lista de {"id", "text" (título), "hostedUrl", "createdAt" (ms), "categories":
{"location", "team", "commitment", "department"}, "workplaceType" ("remote"|"hybrid"|"onsite"),
"descriptionPlain", "lists": [{"text", "content" (HTML)}], "additionalPlain", ...}
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from . import Vaga
from .http import ClienteHTTP
from .texto import html_para_texto


class Lever:
    nome = "lever"

    def __init__(self, empresa: str):
        if not empresa or "/" in empresa:
            raise ValueError(f"empresa Lever inválida: {empresa!r}")
        self.empresa = empresa

    @property
    def url(self) -> str:
        return f"https://api.lever.co/v0/postings/{self.empresa}?mode=json"

    def buscar(self, http: ClienteHTTP) -> list[Vaga]:
        return converter(http.get_json(self.url), self.empresa)


def _data(ms: Any) -> datetime | None:
    if isinstance(ms, (int, float)) and ms > 0:
        return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
    return None


def converter(dados: Any, empresa: str) -> list[Vaga]:
    if not isinstance(dados, list):
        return []
    vagas: list[Vaga] = []
    for p in dados:
        if not isinstance(p, dict) or not p.get("id") or not p.get("text") or not p.get("hostedUrl"):
            continue
        cats = p.get("categories") or {}
        partes = [p.get("descriptionPlain") or html_para_texto(p.get("description") or "")]
        for lista in p.get("lists") or []:
            if isinstance(lista, dict):
                partes.append(str(lista.get("text") or ""))
                partes.append(html_para_texto(str(lista.get("content") or "")))
        partes.append(p.get("additionalPlain") or html_para_texto(p.get("additional") or ""))
        descricao = "\n".join(x for x in partes if x).strip()

        tipo = str(p.get("workplaceType") or "").lower()
        remoto = True if tipo == "remote" else False if tipo in ("hybrid", "onsite", "on-site") else None
        vagas.append(
            Vaga(
                id_fonte=str(p["id"]),
                fonte="lever",
                empresa=empresa,
                cargo=str(p["text"]).strip(),
                localizacao=str(cats.get("location") or "").strip(),
                remoto=remoto,
                descricao=descricao,
                url=str(p["hostedUrl"]),
                data=_data(p.get("createdAt")),
            )
        )
    return vagas
