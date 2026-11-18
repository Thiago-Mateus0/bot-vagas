"""Histórico da sessão de "Entrar com LinkedIn/Google" no perfil do bot: quando passou sozinha e quando caiu.

Serve para o Thiago decidir, com dados da semana de simulação, entre (decisão pendente, ver HANDOFF.md):
  A) o bot clicar em "Entrar" quando o Chromium já preencheu e-mail/senha sozinho (nunca lê/digita senha);
  B) (atual) o bot parar, avisar no celular e o Thiago refazer `bot-vagas login-gupy <link>`.

Guarda só: data/hora, provedor e "passou"/"caiu". Nada de e-mail, cookie, token ou URL com parâmetros.
Arquivo local em pasta_logs()/sessoes.jsonl, permissão 0600.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .copiloto import pasta_logs

EVENTOS = ("passou", "caiu")


def caminho() -> Path:
    return pasta_logs() / "sessoes.jsonl"


def registrar(evento: str, provedor: str, quando: datetime | None = None) -> None:
    if evento not in EVENTOS or not provedor:
        return
    quando = quando or datetime.now(timezone.utc)
    arq = caminho()
    try:
        arq.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(arq, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as f:
            f.write(json.dumps({"quando": quando.isoformat(timespec="seconds"), "provedor": provedor,
                                "evento": evento}) + "\n")
    except OSError:
        pass  # histórico é só diagnóstico: nunca derruba a rodada


def ler() -> list[dict]:
    try:
        linhas = caminho().read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    saida = []
    for l in linhas:
        try:
            d = json.loads(l)
            d["quando"] = datetime.fromisoformat(d["quando"])
        except (ValueError, KeyError, TypeError):
            continue
        saida.append(d)
    return saida


def resumo(agora: datetime | None = None, dias: int = 7) -> list[str]:
    """Uma linha por provedor: última passagem ok, última queda e quedas nos últimos `dias` dias."""
    agora = agora or datetime.now(timezone.utc)
    por: dict[str, list[dict]] = {}
    for d in ler():
        por.setdefault(d["provedor"], []).append(d)
    fmt = lambda dt: dt.astimezone().strftime("%d/%m %H:%M") if dt else "nunca"  # noqa: E731
    saida = []
    for prov, evs in sorted(por.items()):
        ok = max((e["quando"] for e in evs if e["evento"] == "passou"), default=None)
        caiu = max((e["quando"] for e in evs if e["evento"] == "caiu"), default=None)
        quedas = sum(1 for e in evs if e["evento"] == "caiu" and e["quando"] >= agora - timedelta(days=dias))
        saida.append(f"sessão do {prov}: passou sozinha por último em {fmt(ok)}; caiu por último em {fmt(caiu)}; "
                     f"quedas nos últimos {dias} dias: {quedas}")
    return saida
