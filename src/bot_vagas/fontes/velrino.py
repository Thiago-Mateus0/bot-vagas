"""velrino/jobs: quadro de vagas de tecnologia mantido em GitHub Issues (https://github.com/velrino/jobs).

Cada vaga é uma issue aberta com etiquetas (level:intern, region:brazil, work:remote, ...) e um corpo em seções
"## Company", "## Job title", "## Eligible location", "## Original posting and application" etc.

GET https://api.github.com/repos/velrino/jobs/issues?state=open&labels=level:intern,region:brazil&per_page=100
(API pública do GitHub, sem login; limite de 60 requisições/hora por IP, o bot usa poucas.)

O conteúdo vem de issues que QUALQUER pessoa pode abrir: é dado não confiável, como qualquer vaga. Só links
https são aceitos, e o texto vai para o avaliador delimitado como dado (avaliador.delimitar_vaga).
Links da Gupy (empresa.gupy.io/jobs/<id>) viram o MESMO endereço que a busca da Gupy usa, para não duplicar a
vaga e para o modo automático reconhecê-la.
"""

from __future__ import annotations

import base64
import json
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote, urlsplit

from . import Vaga
from .http import ClienteHTTP

REPO = "velrino/jobs"
API = f"https://api.github.com/repos/{REPO}/issues"
MAX_DESCRICAO = 6000

_RE_SECAO = re.compile(r"^##\s+(.+?)\s*$", re.M)
_RE_URL = re.compile(r"https://[^\s<>()\"']+")
_RE_GUPY_JOBS = re.compile(r"^/jobs/(\d+)/?$")

# seções que viram a descrição (o resto é metadado)
SECOES_DESCRICAO = ("Responsibilities", "Requirements", "Compensation", "Application and working conditions")


class Velrino:
    nome = "velrino"

    def __init__(self, niveis: list[str] | None = None, regioes: list[str] | None = None, max_paginas: int = 3):
        self.niveis = [n.strip() for n in (niveis or ["intern"]) if n and n.strip()]
        self.regioes = [r.strip() for r in (regioes or ["brazil"]) if r and r.strip()]
        if not self.niveis:
            raise ValueError("velrino: informe ao menos um nível (ex.: intern)")
        for v in self.niveis + self.regioes:
            if not re.fullmatch(r"[a-z0-9-]+", v):
                raise ValueError(f"velrino: etiqueta inválida: {v!r}")
        self.max_paginas = max(1, min(int(max_paginas), 10))
        self.token = ",".join(self.niveis)  # rótulo nos logs do `buscar`

    def url_pagina(self, nivel: str, regiao: str | None, pagina: int) -> str:
        etiquetas = [f"level:{nivel}"] + ([f"region:{regiao}"] if regiao else [])
        return (f"{API}?state=open&per_page=100&page={pagina}&labels="
                + quote(",".join(etiquetas), safe=","))

    def buscar(self, http: ClienteHTTP) -> list[Vaga]:
        vistas: dict[str, Vaga] = {}
        for nivel in self.niveis:
            for regiao in (self.regioes or [None]):
                for pagina in range(1, self.max_paginas + 1):
                    itens = http.get_json(self.url_pagina(nivel, regiao, pagina))
                    if not isinstance(itens, list):
                        break
                    for v in converter(itens):
                        vistas.setdefault(v.id_fonte, v)
                    if len(itens) < 100:
                        break
        return list(vistas.values())


def _secoes(corpo: str) -> dict[str, str]:
    partes = _RE_SECAO.split(corpo or "")
    # partes = [antes, titulo1, texto1, titulo2, texto2, ...]
    return {partes[i].strip(): partes[i + 1].strip() for i in range(1, len(partes) - 1, 2)}


def url_gupy_canonica(url: str) -> str:
    """https://empresa.gupy.io/jobs/123 -> https://empresa.gupy.io/job/<base64 {"jobId":123,...}>?jobBoardSource=gupy_portal
    (o formato que a busca da Gupy e o portal usam). Outras URLs voltam iguais."""
    try:
        u = urlsplit(url)
    except ValueError:
        return url
    host = (u.hostname or "").lower()
    m = _RE_GUPY_JOBS.match(u.path or "")
    if not m or not (host.endswith(".gupy.io") and host != "portal.gupy.io"):
        return url
    dados = json.dumps({"jobId": int(m.group(1)), "source": "gupy_portal"}, separators=(",", ":"))
    codigo = base64.b64encode(dados.encode()).decode()
    return f"https://{host}/job/{codigo}?jobBoardSource=gupy_portal"


def _link(secoes: dict[str, str], corpo: str) -> str | None:
    texto = secoes.get("Original posting and application") or ""
    m = _RE_URL.search(texto) or None
    if m is None:
        return None
    url = m.group(0).rstrip(".,;")
    try:
        u = urlsplit(url)
    except ValueError:
        return None
    if u.scheme != "https" or not u.hostname or u.hostname.endswith("github.com"):
        return None
    return url_gupy_canonica(url)


def _data(valor: str) -> datetime | None:
    try:
        return datetime.fromisoformat(valor.strip()[:10]).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def converter(itens: Any) -> list[Vaga]:
    if not isinstance(itens, list):
        return []
    vagas: list[Vaga] = []
    for it in itens:
        if not isinstance(it, dict) or "pull_request" in it or it.get("state") != "open":
            continue
        corpo = str(it.get("body") or "")
        s = _secoes(corpo)
        empresa = (s.get("Company") or "").strip()[:200]
        cargo = (s.get("Job title") or "").strip()[:300]
        url = _link(s, corpo)
        if not empresa or not cargo or not url or "number" not in it:
            continue
        etiquetas = {str((e or {}).get("name", "")) for e in it.get("labels") or [] if isinstance(e, dict)}
        if "work:remote" in etiquetas:
            remoto: bool | None = True
        elif etiquetas & {"work:on-site", "work:hybrid"}:
            remoto = False
        else:
            remoto = None
        local = (s.get("Eligible location") or "").strip()[:200]
        modalidade = next((e.split(":", 1)[1] for e in sorted(etiquetas) if e.startswith("work:")), "")
        descricao = "\n\n".join(f"{t}:\n{s[t]}" for t in SECOES_DESCRICAO if s.get(t))
        if modalidade:
            descricao = f"Modalidade: {modalidade}. Local permitido: {local}\n\n{descricao}"
        vagas.append(Vaga(
            id_fonte=str(it["number"]),
            fonte="velrino",
            empresa=empresa,
            cargo=cargo,
            localizacao=local,
            remoto=remoto,
            descricao=descricao[:MAX_DESCRICAO],
            url=url,
            data=_data(s.get("Published on") or "") or _data(str(it.get("created_at") or "")),
        ))
    return vagas
