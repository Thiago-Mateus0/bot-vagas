"""Configuração e limites do modo 100% automático (automacao.yaml, local e fora do git).

Decisões do Thiago (2026-11-16): envio sem clique dele, aceitando o risco de banimento; o bot marca termos/LGPD;
perguntas abertas respondidas pelo Haiku (só com `usar_modelo_para_perguntas_abertas: true`, depois do teto na
conta da Anthropic); primeira semana em SIMULAÇÃO (vai até a última tela, tira screenshot e não envia).

Continua proibido: resolver captcha, qualquer evasão de detecção, inventar dado pessoal.
"""

from __future__ import annotations

import json
import os
import random
from dataclasses import dataclass, field
from datetime import datetime, time as dtime
from pathlib import Path
from typing import Any

import yaml

from .perfil import RAIZ_PROJETO

CONFIG_PADRAO = RAIZ_PROJETO / "automacao.yaml"
MODOS = ("simulacao", "envio")
SITES = ("gupy",)  # LinkedIn e Indeed: próxima etapa (ver HANDOFF.md)


def caminho_config() -> Path:
    env = os.environ.get("BOT_VAGAS_AUTOMACAO")
    return Path(env) if env else CONFIG_PADRAO


@dataclass(frozen=True)
class ConfigAutomacao:
    ativo: bool = False                    # `rodar` (timer) só se candidata se ativo
    modo: str = "simulacao"                # "simulacao" | "envio"
    aceitar_termos: bool = True            # marca caixas de termos/LGPD obrigatórias
    usar_modelo_para_perguntas_abertas: bool = False  # Haiku; ligar só depois do teto na conta
    entrar_com_linkedin: bool = False      # na tela de login da Gupy, clica no botão "LinkedIn" (nada digitado)
    digitar_senha_gupy: bool = False       # Gupy pediu login: digita login/senha do chaveiro (bot-vagas senha-gupy)
    exigir_nota_do_modelo: bool = False    # True: só vagas "aprovada" (avaliar --real); False: as que passaram no filtro
    limite_diario: int = 10
    limite_por_site: dict[str, int] = field(default_factory=lambda: {"gupy": 10})
    janela_inicio: str = "14:00"
    janela_fim: str = "17:00"
    pausa_min_s: float = 120.0
    pausa_max_s: float = 420.0
    max_telas_por_vaga: int = 15
    navegador_visivel: str = "auto"        # "auto" (visível se houver tela) | "sim" | "nao"

    @property
    def envia_de_verdade(self) -> bool:
        return self.modo == "envio"

    def visivel(self) -> bool:
        if self.navegador_visivel == "sim":
            return True
        if self.navegador_visivel == "nao":
            return False
        return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


class ConfigInvalida(ValueError):
    pass


def _hhmm(valor: str) -> dtime:
    try:
        h, m = str(valor).split(":")
        return dtime(int(h), int(m))
    except (ValueError, TypeError):
        raise ConfigInvalida(f"horário inválido: {valor!r} (use HH:MM)") from None


def carregar_config(caminho: Path | str | None = None) -> ConfigAutomacao:
    caminho = Path(caminho) if caminho is not None else caminho_config()
    if not caminho.exists():
        return ConfigAutomacao()
    dados = yaml.safe_load(caminho.read_text(encoding="utf-8")) or {}
    if not isinstance(dados, dict):
        raise ConfigInvalida(f"{caminho.name}: esperado um mapeamento YAML")
    base = ConfigAutomacao()
    campos = {k: dados[k] for k in base.__dataclass_fields__ if k in dados}
    desconhecidos = sorted(set(dados) - set(base.__dataclass_fields__))
    if desconhecidos:
        raise ConfigInvalida(f"{caminho.name}: chaves desconhecidas: {', '.join(desconhecidos)}")
    cfg = ConfigAutomacao(**campos)
    if cfg.modo not in MODOS:
        raise ConfigInvalida(f"{caminho.name}: modo deve ser 'simulacao' ou 'envio'")
    if cfg.navegador_visivel not in ("auto", "sim", "nao"):
        raise ConfigInvalida(f"{caminho.name}: navegador_visivel deve ser auto, sim ou nao")
    if cfg.limite_diario < 0 or any(int(v) < 0 for v in cfg.limite_por_site.values()):
        raise ConfigInvalida(f"{caminho.name}: limites não podem ser negativos")
    if cfg.pausa_min_s < 0 or cfg.pausa_max_s < cfg.pausa_min_s:
        raise ConfigInvalida(f"{caminho.name}: pausa_max_s precisa ser >= pausa_min_s >= 0")
    _hhmm(cfg.janela_inicio), _hhmm(cfg.janela_fim)
    return cfg


def dentro_da_janela(cfg: ConfigAutomacao, agora: datetime | None = None) -> bool:
    agora = agora or datetime.now()
    ini, fim = _hhmm(cfg.janela_inicio), _hhmm(cfg.janela_fim)
    return ini <= agora.time() <= fim


def pausa_aleatoria(cfg: ConfigAutomacao, rng: random.Random | None = None) -> float:
    return (rng or random).uniform(cfg.pausa_min_s, cfg.pausa_max_s)


def site_da_url(url: str) -> str | None:
    from urllib.parse import urlsplit

    try:
        host = (urlsplit(url).hostname or "").lower()
    except ValueError:
        return None
    if host == "gupy.io" or host.endswith(".gupy.io"):
        return "gupy"
    return None


def _detalhes(row) -> dict[str, Any]:
    try:
        d = json.loads(row["detalhes"]) if row["detalhes"] else {}
    except ValueError:
        return {}
    return d if isinstance(d, dict) else {}


def tentativas_hoje(banco, hoje: str | None = None) -> dict[str, int]:
    """Tentativas automáticas (simulação ou envio) por site no dia local `hoje` (AAAA-MM-DD)."""
    hoje = hoje or datetime.now().date().isoformat()
    cont: dict[str, int] = {}
    for row in banco.listar():
        auto = _detalhes(row).get("automatico")
        if not isinstance(auto, dict):
            continue
        for t in auto.get("tentativas") or []:
            if str(t.get("dia")) == hoje:
                site = str(t.get("site") or "?")
                cont[site] = cont.get(site, 0) + 1
    return cont


def vagas_elegiveis(banco, cfg: ConfigAutomacao) -> list:
    """Vagas da Gupy que o bot pode tentar agora, sem nunca repetir uma enviada, pulada ou em andamento.
    Em modo envio, as que foram só simuladas voltam a ser elegíveis (é o objetivo da semana de simulação)."""
    status = ["aprovada"] if cfg.exigir_nota_do_modelo else ["aprovada", "nova", "preparada"]
    if cfg.envia_de_verdade:
        status.append("simulada")
    saida = []
    for row in banco.listar(status):
        if site_da_url(row["url"]) not in SITES:
            continue
        d = _detalhes(row)
        if row["status"] in ("nova", "preparada") and d.get("filtro") != "passou":
            continue  # não filtrada/bloqueada, ou preparada à mão por você (essa é sua, não do bot)
        saida.append(row)
    return saida


EXEMPLO = """# Modo 100% automático. Copie para automacao.yaml (fica FORA do git).
# O bot se candidata SOZINHO às vagas da Gupy que passaram no filtro, inclusive aceitando termos/LGPD.
# Risco aceito por você: banimento da conta. Continua proibido: resolver captcha e disfarçar o robô.

ativo: false            # true = o `bot-vagas rodar` (timer das 14h) também se candidata
modo: simulacao         # simulacao = vai até a última tela, tira screenshot e NÃO envia; envio = envia de verdade
aceitar_termos: true    # marca as caixas obrigatórias de termos/política de privacidade/LGPD
usar_modelo_para_perguntas_abertas: false   # true SÓ depois de definir o teto no painel da Anthropic (custa API)
entrar_com_linkedin: false  # true = se a Gupy pedir login, o bot clica no botão "LinkedIn" (usa a sessão do LinkedIn
                            # salva pelo login-gupy; nunca digita e-mail/senha; se o LinkedIn pedir senha, ele para)
digitar_senha_gupy: false  # true = se a Gupy pedir login, o bot DIGITA o login e a senha da Gupy guardados no
                           # chaveiro do sistema (`bot-vagas senha-gupy`) e clica em "Acessar conta". Só em gupy.io,
                           # no máximo 1 vez por rodada; código/verificação/captcha => para e avisa.
exigir_nota_do_modelo: false                # true = só vagas aprovadas pelo `avaliar --real`
limite_diario: 10
limite_por_site:
  gupy: 10
janela_inicio: "14:00"  # fora deste horário o bot não começa candidaturas (use --agora para forçar)
janela_fim: "17:00"
pausa_min_s: 120        # pausa aleatória entre uma candidatura e outra
pausa_max_s: 420
max_telas_por_vaga: 15
navegador_visivel: auto # auto = janela visível se houver tela; sim; nao (headless)
"""
