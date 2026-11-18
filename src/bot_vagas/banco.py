"""Persistência em SQLite (vagas.db, gitignored).

Tabelas:
- vagas: uma linha por vaga; `chave` (empresa+cargo+url normalizados) garante dedupe entre
  fontes. Vaga já vista nunca é reprocessada.
- chamadas_modelo: usage de cada chamada à API (para o contador de gasto).
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlsplit, urlunsplit

from .fontes import Vaga
from .fontes.texto import normalizar
from .perfil import RAIZ_PROJETO

BANCO_PADRAO = RAIZ_PROJETO / "vagas.db"

# "preparada" (Etapa 4): candidatura preparada manualmente (PDF + pacote em saida/); o envio é do Thiago.
# "em_candidatura": o copiloto (candidatar) está/esteve assistindo o formulário; "enviada"/"desistiu": o
# Thiago informou via `marcar` (o bot nunca envia, logo nunca sabe sozinho). Sem mudança de schema.
# "simulada"/"pulada" (reservados para um modo de envio automático mantido fora deste repositório): o bot foi até a última tela sem enviar (simulação) ou
# desistiu da vaga sozinho (campo que não sabe, captcha, botão não encontrado...); o motivo fica em `motivo`.
STATUS = ("nova", "bloqueada", "avaliada", "abaixo_do_corte", "aprovada", "preparada",
          "em_candidatura", "simulada", "pulada", "enviada", "desistiu")
STATUS_MANUAIS = ("enviada", "desistiu", "em_candidatura")  # os que `marcar` aceita

_SCHEMA = """
CREATE TABLE IF NOT EXISTS vagas (
    chave        TEXT PRIMARY KEY,
    fonte        TEXT NOT NULL,
    id_fonte     TEXT NOT NULL,
    empresa      TEXT NOT NULL,
    cargo        TEXT NOT NULL,
    localizacao  TEXT NOT NULL DEFAULT '',
    remoto       INTEGER,
    descricao    TEXT NOT NULL DEFAULT '',
    url          TEXT NOT NULL,
    data_vaga    TEXT,
    status       TEXT NOT NULL DEFAULT 'nova',
    nota         REAL,
    motivo       TEXT,
    detalhes     TEXT,
    criado_em    TEXT NOT NULL,
    atualizado_em TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_vagas_status ON vagas(status);
CREATE TABLE IF NOT EXISTS chamadas_modelo (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    quando        TEXT NOT NULL,
    modelo        TEXT NOT NULL,
    finalidade    TEXT NOT NULL,
    chave_vaga    TEXT,
    tokens_entrada INTEGER NOT NULL DEFAULT 0,
    tokens_saida   INTEGER NOT NULL DEFAULT 0,
    tokens_cache_leitura INTEGER NOT NULL DEFAULT 0,
    tokens_cache_escrita INTEGER NOT NULL DEFAULT 0,
    custo_usd     REAL NOT NULL DEFAULT 0
);
"""


def _agora() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalizar_url(url: str) -> str:
    """Sem esquema, sem query/fragment (rastreadores), sem barra final, minúsculas."""
    partes = urlsplit(url.strip())
    host = (partes.netloc or "").lower()
    if host.startswith("www."):
        host = host[4:]
    caminho = re.sub(r"/+$", "", partes.path or "")
    return urlunsplit(("", host, caminho, "", "")).lstrip("/").lower()


def chave_vaga(empresa: str, cargo: str, url: str) -> str:
    base = "|".join([normalizar(empresa), normalizar(cargo), normalizar_url(url)])
    return hashlib.sha1(base.encode("utf-8")).hexdigest()


class Banco:
    def __init__(self, caminho: Path | str = BANCO_PADRAO):
        self.caminho = str(caminho)
        self.con = sqlite3.connect(self.caminho)
        self.con.row_factory = sqlite3.Row
        self.con.executescript(_SCHEMA)

    def close(self) -> None:
        self.con.close()

    def __enter__(self) -> "Banco":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # --- vagas ----------------------------------------------------------

    def salvar_vagas(self, vagas: Iterable[Vaga]) -> int:
        """Insere só as vagas ainda não vistas. Devolve quantas foram novas."""
        novas = 0
        agora = _agora()
        # mesma vaga vinda de duas fontes (ex.: Gupy e velrino/jobs) com nome de empresa/cargo escrito diferente:
        # o endereço normalizado (sem query) é o mesmo -> não duplica
        urls = {normalizar_url(r[0]) for r in self.con.execute("SELECT url FROM vagas")}
        with self.con:
            for v in vagas:
                url_norm = normalizar_url(v.url)
                if url_norm in urls:
                    continue
                chave = chave_vaga(v.empresa, v.cargo, v.url)
                cur = self.con.execute(
                    """INSERT OR IGNORE INTO vagas
                       (chave, fonte, id_fonte, empresa, cargo, localizacao, remoto, descricao, url,
                        data_vaga, status, criado_em, atualizado_em)
                       VALUES (?,?,?,?,?,?,?,?,?,?,'nova',?,?)""",
                    (
                        chave, v.fonte, v.id_fonte, v.empresa, v.cargo, v.localizacao,
                        None if v.remoto is None else int(v.remoto), v.descricao, v.url,
                        v.data.isoformat() if v.data else None, agora, agora,
                    ),
                )
                novas += cur.rowcount
                urls.add(url_norm)
        return novas

    def listar(self, status: str | Iterable[str] | None = None) -> list[sqlite3.Row]:
        if status is None:
            return self.con.execute("SELECT * FROM vagas ORDER BY nota DESC NULLS LAST, criado_em DESC").fetchall()
        lista = [status] if isinstance(status, str) else list(status)
        marcas = ",".join("?" * len(lista))
        return self.con.execute(
            f"SELECT * FROM vagas WHERE status IN ({marcas}) ORDER BY nota DESC NULLS LAST, criado_em DESC", lista
        ).fetchall()

    def atualizar_status(self, chave: str, status: str, *, nota: float | None = None,
                         motivo: str | None = None, detalhes: str | None = None) -> None:
        if status not in STATUS:
            raise ValueError(f"status inválido: {status}")
        with self.con:
            self.con.execute(
                "UPDATE vagas SET status=?, nota=COALESCE(?, nota), motivo=COALESCE(?, motivo), "
                "detalhes=COALESCE(?, detalhes), atualizado_em=? WHERE chave=?",
                (status, nota, motivo, detalhes, _agora(), chave),
            )

    def contar_por_status(self) -> dict[str, int]:
        rows = self.con.execute("SELECT status, COUNT(*) n FROM vagas GROUP BY status").fetchall()
        return {r["status"]: r["n"] for r in rows}

    # --- gasto ----------------------------------------------------------

    def registrar_chamada(self, *, modelo: str, finalidade: str, chave_vaga: str | None,
                          tokens_entrada: int, tokens_saida: int, tokens_cache_leitura: int,
                          tokens_cache_escrita: int, custo_usd: float, quando: str | None = None) -> None:
        with self.con:
            self.con.execute(
                """INSERT INTO chamadas_modelo (quando, modelo, finalidade, chave_vaga, tokens_entrada,
                   tokens_saida, tokens_cache_leitura, tokens_cache_escrita, custo_usd)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (quando or _agora(), modelo, finalidade, chave_vaga, tokens_entrada, tokens_saida,
                 tokens_cache_leitura, tokens_cache_escrita, custo_usd),
            )

    def gasto_no_mes(self, ano: int, mes: int) -> float:
        prefixo = f"{ano:04d}-{mes:02d}-"
        row = self.con.execute(
            "SELECT COALESCE(SUM(custo_usd), 0) s FROM chamadas_modelo WHERE quando LIKE ?", (prefixo + "%",)
        ).fetchone()
        return float(row["s"])

    def resumo_gasto_no_mes(self, ano: int, mes: int) -> dict[str, Any]:
        """Mesmos campos de resumo_gasto, restritos ao mês (somente leitura; usado pelo painel)."""
        prefixo = f"{ano:04d}-{mes:02d}-"
        row = self.con.execute(
            "SELECT COUNT(*) n, COALESCE(SUM(custo_usd),0) s, COALESCE(SUM(tokens_entrada),0) e, "
            "COALESCE(SUM(tokens_saida),0) o FROM chamadas_modelo WHERE quando LIKE ?", (prefixo + "%",)
        ).fetchone()
        return {"chamadas": row["n"], "custo_usd": float(row["s"]), "tokens_entrada": row["e"], "tokens_saida": row["o"]}

    def listar_chamadas(self, limite: int = 20) -> list[sqlite3.Row]:
        """Últimas chamadas ao modelo, mais recentes primeiro (somente leitura; usado pelo painel)."""
        return self.con.execute(
            "SELECT c.*, v.empresa, v.cargo FROM chamadas_modelo c LEFT JOIN vagas v ON v.chave = c.chave_vaga "
            "ORDER BY c.quando DESC, c.id DESC LIMIT ?", (int(limite),)
        ).fetchall()

    def buscar_por_chave(self, chave: str) -> sqlite3.Row | None:
        return self.con.execute("SELECT * FROM vagas WHERE chave = ?", (chave,)).fetchone()

    def buscar_por_url(self, url: str) -> sqlite3.Row | None:
        """Vaga cuja URL normalizada (sem esquema/query/www) coincide. Exata primeiro, depois varredura."""
        row = self.con.execute("SELECT * FROM vagas WHERE url = ?", (url.strip(),)).fetchone()
        if row is not None:
            return row
        alvo = normalizar_url(url)
        if not alvo:
            return None
        for r in self.con.execute("SELECT chave, url FROM vagas"):
            if normalizar_url(r["url"]) == alvo:
                return self.buscar_por_chave(r["chave"])
        return None

    def buscar(self, chave_ou_url: str) -> sqlite3.Row | None:
        """Aceita a chave (sha1 hex) ou a URL da vaga."""
        s = (chave_ou_url or "").strip()
        if re.fullmatch(r"[0-9a-f]{40}", s):
            return self.buscar_por_chave(s)
        return self.buscar_por_url(s)

    def resumo_gasto(self) -> dict[str, Any]:
        row = self.con.execute(
            "SELECT COUNT(*) n, COALESCE(SUM(custo_usd),0) s, COALESCE(SUM(tokens_entrada),0) e, "
            "COALESCE(SUM(tokens_saida),0) o FROM chamadas_modelo"
        ).fetchone()
        return {"chamadas": row["n"], "custo_usd": float(row["s"]), "tokens_entrada": row["e"], "tokens_saida": row["o"]}
