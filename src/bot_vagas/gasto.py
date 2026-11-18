"""Contador de gasto mínimo (docs/DECISOES.md, "Modelo e custo").

Lê preços e teto de custos.yaml, registra o `usage` de cada chamada no SQLite e bloqueia
novas avaliações quando o gasto do mês atinge o teto.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from .banco import Banco
from .perfil import RAIZ_PROJETO

CUSTOS_PADRAO = RAIZ_PROJETO / "custos.yaml"


class TetoAtingido(RuntimeError):
    pass


@dataclass(frozen=True)
class Precos:
    entrada: float = 0.10
    saida: float = 0.50
    cache_leitura: float = 0.01
    cache_escrita: float = 0.125


@dataclass(frozen=True)
class ConfigCustos:
    modelo: str = "claude-haiku-5-5"
    precos: Precos = Precos()
    teto_mensal_usd: float = 3.0


def carregar_custos(caminho: Path | str = CUSTOS_PADRAO) -> ConfigCustos:
    with open(caminho, encoding="utf-8") as f:
        d = yaml.safe_load(f) or {}
    p = d.get("precos_usd_por_milhao") or {}
    return ConfigCustos(
        modelo=str(d.get("modelo", "claude-haiku-5-5")),
        precos=Precos(
            entrada=float(p.get("entrada", 0.10)),
            saida=float(p.get("saida", 0.50)),
            cache_leitura=float(p.get("cache_leitura", 0.01)),
            cache_escrita=float(p.get("cache_escrita", 0.125)),
        ),
        teto_mensal_usd=float(d.get("teto_mensal_usd", 3.0)),
    )


def extrair_usage(resposta: Any) -> dict[str, int]:
    u = getattr(resposta, "usage", None)
    def g(nome: str) -> int:
        v = getattr(u, nome, None) if u is not None else None
        return int(v or 0)
    return {
        "entrada": g("input_tokens"),
        "saida": g("output_tokens"),
        "cache_leitura": g("cache_read_input_tokens"),
        "cache_escrita": g("cache_creation_input_tokens"),
    }


def calcular_custo(usage: dict[str, int], precos: Precos) -> float:
    return (
        usage.get("entrada", 0) * precos.entrada
        + usage.get("saida", 0) * precos.saida
        + usage.get("cache_leitura", 0) * precos.cache_leitura
        + usage.get("cache_escrita", 0) * precos.cache_escrita
    ) / 1_000_000


class Contador:
    def __init__(self, banco: Banco, config: ConfigCustos, agora: datetime | None = None):
        self.banco = banco
        self.config = config
        self._agora = agora  # injetável nos testes

    def agora(self) -> datetime:
        return self._agora or datetime.now(timezone.utc)

    def gasto_do_mes(self) -> float:
        a = self.agora()
        return self.banco.gasto_no_mes(a.year, a.month)

    def pode_gastar(self) -> bool:
        return self.gasto_do_mes() < self.config.teto_mensal_usd

    def exigir_orcamento(self) -> None:
        gasto = self.gasto_do_mes()
        if gasto >= self.config.teto_mensal_usd:
            raise TetoAtingido(
                f"teto mensal atingido: US$ {gasto:.4f} de US$ {self.config.teto_mensal_usd:.2f}. "
                "Nenhuma avaliação nova será feita este mês (ajuste em custos.yaml se quiser)."
            )

    def registrar(self, resposta: Any, *, finalidade: str, chave_vaga: str | None = None) -> float:
        usage = extrair_usage(resposta)
        custo = calcular_custo(usage, self.config.precos)
        self.banco.registrar_chamada(
            modelo=getattr(resposta, "model", None) or self.config.modelo,
            finalidade=finalidade,
            chave_vaga=chave_vaga,
            tokens_entrada=usage["entrada"],
            tokens_saida=usage["saida"],
            tokens_cache_leitura=usage["cache_leitura"],
            tokens_cache_escrita=usage["cache_escrita"],
            custo_usd=custo,
            quando=self.agora().isoformat(timespec="seconds"),
        )
        return custo
