"""Respostas fixas de formulário (respostas.yaml): DADOS PESSOAIS, só para o arquivo local.

Este módulo NUNCA é importado pelos módulos que montam prompts (avaliador, plano_llm) e o
conteúdo daqui jamais entra em uma chamada ao modelo. Ele só alimenta a tabela "respostas
prontas" do pacote de candidatura em saida/. Campo nulo = "PREENCHER / bot não responde".
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .perfil import RAIZ_PROJETO

RESPOSTAS_PADRAO = RAIZ_PROJETO / "respostas.yaml"
MARCA_NULO = "PREENCHER / bot não responde"

# (chave no yaml, rótulo exibido). A ordem define a tabela.
CAMPOS: tuple[tuple[str, str], ...] = (
    ("pretensao_salarial.brl_mensal_minimo", "Pretensão salarial (R$/mês) — SÓ se o formulário perguntar"),
    ("pretensao_salarial.usd_mensal_minimo", "Pretensão salarial (US$/mês) — SÓ se o formulário perguntar"),
    ("pretensao_salarial.observacao", "Observação sobre salário"),
    ("disponibilidade.inicio", "Disponibilidade para início"),
    ("disponibilidade.carga_horaria", "Carga horária"),
    ("disponibilidade.turno_preferido", "Turno preferido"),
    ("tipo_contrato_aceito", "Tipos de contrato aceitos"),
    ("localizacao.cidade", "Cidade"),
    ("localizacao.estado", "Estado"),
    ("localizacao.pais", "País"),
    ("localizacao.aceita_presencial_em", "Aceita presencial em"),
    ("localizacao.aceita_remoto", "Aceita remoto"),
    ("localizacao.aceita_mudar_de_cidade", "Aceita mudar de cidade"),
    ("autorizacao_trabalho.brasil", "Autorização de trabalho no Brasil"),
    ("autorizacao_trabalho.exterior", "Exterior"),
    ("idiomas.portugues", "Português"),
    ("idiomas.ingles", "Inglês"),
    ("dados_demograficos.genero", "Gênero (pergunta voluntária)"),
    ("dados_demograficos.raca_cor", "Raça/cor (pergunta voluntária)"),
    ("dados_demograficos.pcd", "PCD (pergunta voluntária)"),
    ("extras.data_nascimento", "Data de nascimento"),
    ("extras.previsao_formatura", "Previsão de formatura"),
    ("extras.possui_cnh", "Possui CNH"),
    ("extras.parente_na_empresa", "Parente na empresa"),
    ("extras.ja_trabalhou_na_empresa", "Já trabalhou na empresa"),
    ("extras.como_conheceu_a_vaga", "Como conheceu a vaga"),
    ("extras.aceita_viajar", "Aceita viajar"),
    ("extras.aceita_trabalhar_fins_de_semana", "Aceita trabalhar fins de semana"),
)


def carregar_respostas(caminho: Path | str = RESPOSTAS_PADRAO) -> dict[str, Any] | None:
    """Devolve o dicionário do yaml, ou None se o arquivo não existe."""
    caminho = Path(caminho)
    if not caminho.exists():
        return None
    with open(caminho, encoding="utf-8") as f:
        d = yaml.safe_load(f) or {}
    if not isinstance(d, dict):
        raise ValueError(f"respostas.yaml inválido em {caminho}")
    return d


def _buscar(d: dict[str, Any], chave: str) -> Any:
    atual: Any = d
    for parte in chave.split("."):
        if not isinstance(atual, dict) or parte not in atual:
            return None
        atual = atual[parte]
    return atual


def formatar_valor(valor: Any) -> str:
    if valor is None or valor == "":
        return MARCA_NULO
    if valor is True:
        return "sim"
    if valor is False:
        return "não"
    if isinstance(valor, list):
        return ", ".join(formatar_valor(v) for v in valor) if valor else MARCA_NULO
    return str(valor)


def tabela_respostas(respostas: dict[str, Any] | None) -> list[tuple[str, str, bool]]:
    """Lista de (rótulo, valor formatado, é_nulo). Só lê o yaml: nenhum modelo envolvido."""
    linhas = []
    for chave, rotulo in CAMPOS:
        valor = _buscar(respostas or {}, chave)
        texto = formatar_valor(valor)
        linhas.append((rotulo, texto, texto == MARCA_NULO))
    return linhas


def valores_sensiveis(respostas: dict[str, Any] | None) -> list[str]:
    """Strings do yaml que NUNCA podem aparecer em um prompt (usado em testes/asserções)."""
    saida: list[str] = []

    def andar(v: Any) -> None:
        if isinstance(v, dict):
            for x in v.values():
                andar(x)
        elif isinstance(v, list):
            for x in v:
                andar(x)
        elif isinstance(v, (str, int, float)) and not isinstance(v, bool):
            s = str(v)
            if len(s) >= 4:
                saida.append(s)

    andar(respostas or {})
    return saida
