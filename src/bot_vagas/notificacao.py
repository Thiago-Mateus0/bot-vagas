"""Aviso no celular via ntfy.sh quando o bot/copiloto PAUSA. Opcional e DESLIGADO por padrão.

Configuração em `notificacao.yaml` (local, fora do git; modelo em notificacao.example.yaml):

    ntfy:
      ativo: true
      servidor: https://ntfy.sh
      topico: um-nome-dificil-de-adivinhar

Regras:
- Só textos FIXOS e genéricos (tabela MENSAGENS). Nada de empresa, cargo, link, nome, e-mail, valores de
  respostas.yaml ou texto da vaga: `notificar` só aceita a chave do evento, nunca texto livre.
- O tópico do ntfy funciona como uma senha (quem souber o nome lê os avisos): fica só no arquivo local
  e nunca é impresso.
- Falha de rede nunca derruba o bot: vira uma linha de aviso no terminal e segue.
- O mesmo evento não é reenviado em menos de `intervalo_min_s` segundos (padrão 10 min).
"""

from __future__ import annotations

import json
import re
import sys
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

import yaml

from .perfil import RAIZ_PROJETO

CONFIG_PADRAO = RAIZ_PROJETO / "notificacao.yaml"


def caminho_config() -> Path:
    """BOT_VAGAS_NOTIFICACAO troca o arquivo (os testes apontam para um arquivo inexistente)."""
    import os

    env = os.environ.get("BOT_VAGAS_NOTIFICACAO")
    return Path(env) if env else CONFIG_PADRAO
SERVIDOR_PADRAO = "https://ntfy.sh"
TIMEOUT_S = 5.0

# evento -> (título, mensagem). SEM dados pessoais e sem nada da vaga.
MENSAGENS: dict[str, tuple[str, str]] = {
    "login": ("bot-vagas: precisa de voce",
              "O copiloto pausou: a pagina pede login, captcha ou verificacao. Volte ao computador."),
    "alarme_fonte": ("bot-vagas: rodada pausada",
                     "Uma fonte de vagas mudou de formato ou bloqueou o acesso. Nada foi contornado; veja o terminal."),
    "teto": ("bot-vagas: teto de gasto",
             "O contador de gasto chegou ao teto mensal; as avaliacoes pararam."),
    "parada": ("bot-vagas: parado",
               "A execucao parou porque o botao PARAR foi acionado."),
    "teste": ("bot-vagas: teste",
              "Notificacao de teste do bot-vagas. Se chegou, esta funcionando."),
}

_TOPICO_VALIDO = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

Enviar = Callable[[str, bytes, dict[str, str]], None]


@dataclass(frozen=True)
class ConfigNotificacao:
    ativo: bool = False
    servidor: str = SERVIDOR_PADRAO
    topico: str | None = None
    intervalo_min_s: float = 600.0

    @property
    def utilizavel(self) -> bool:
        return self.ativo and bool(self.topico)


def carregar_config(caminho: Path | str | None = None, log=print) -> ConfigNotificacao:
    """Arquivo ausente, `ativo: false` ou configuração inválida = desligado (com aviso no último caso)."""
    caminho = Path(caminho) if caminho is not None else caminho_config()
    if not caminho.exists():
        return ConfigNotificacao()
    try:
        dados = yaml.safe_load(caminho.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        log(f"aviso: não consegui ler {caminho.name}; notificações desligadas.", file=sys.stderr)
        return ConfigNotificacao()
    ntfy: Any = dados.get("ntfy") if isinstance(dados, dict) else None
    if not isinstance(ntfy, dict) or not ntfy.get("ativo"):
        return ConfigNotificacao()
    servidor = str(ntfy.get("servidor") or SERVIDOR_PADRAO).rstrip("/")
    topico = str(ntfy.get("topico") or "").strip()
    u = urlsplit(servidor)
    if u.scheme != "https" or not u.netloc or u.path not in ("", "/") or u.query or u.username:
        log(f"aviso: {caminho.name}: 'servidor' precisa ser https://host (sem caminho); notificações desligadas.",
            file=sys.stderr)
        return ConfigNotificacao()
    if not _TOPICO_VALIDO.match(topico):
        log(f"aviso: {caminho.name}: 'topico' inválido (use só letras, números, - e _, até 64); notificações desligadas.",
            file=sys.stderr)
        return ConfigNotificacao()
    try:
        intervalo = float(ntfy.get("intervalo_min_s", 600))
    except (TypeError, ValueError):
        intervalo = 600.0
    return ConfigNotificacao(ativo=True, servidor=servidor, topico=topico, intervalo_min_s=max(0.0, intervalo))


def _enviar_http(url: str, corpo: bytes, cabecalhos: dict[str, str]) -> None:
    req = urllib.request.Request(url, data=corpo, headers=cabecalhos, method="POST")
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:  # noqa: S310 (https validado em carregar_config)
        r.read(256)


class Notificador:
    """Chamável: `notificador("login")`. Devolve True se enviou. Nunca levanta por falha de rede."""

    def __init__(self, config: ConfigNotificacao | None = None, *, enviar: Enviar | None = None, log=print,
                 relogio: Callable[[], float] = time.monotonic):
        self.config = config or ConfigNotificacao()
        self._enviar = enviar or _enviar_http
        self._log = log
        self._relogio = relogio
        self._ultimo: dict[str, float] = {}
        self.enviados: list[str] = []  # só as chaves dos eventos (para testes/diagnóstico)

    @property
    def ativo(self) -> bool:
        return self.config.utilizavel

    def __call__(self, evento: str) -> bool:
        if evento not in MENSAGENS:
            raise ValueError(f"evento de notificação desconhecido: {evento!r}")  # erro de programação
        if not self.ativo:
            return False
        agora = self._relogio()
        ultimo = self._ultimo.get(evento)
        if ultimo is not None and agora - ultimo < self.config.intervalo_min_s:
            return False
        titulo, mensagem = MENSAGENS[evento]
        # publicação em JSON na raiz do servidor (evita cabeçalhos com acento)
        corpo = json.dumps({"topic": self.config.topico, "title": titulo, "message": mensagem,
                            "tags": ["robot"]}, ensure_ascii=True).encode("ascii")
        try:
            self._enviar(self.config.servidor + "/", corpo, {"Content-Type": "application/json"})
        except Exception as e:  # rede, DNS, HTTP 4xx/5xx: nunca derruba o bot
            self._log(f"aviso: notificação ntfy não enviada ({type(e).__name__}); seguindo.", file=sys.stderr)
            return False
        self._ultimo[evento] = agora
        self.enviados.append(evento)
        return True


def notificador_padrao(caminho: Path | str | None = None, log=print) -> Notificador:
    return Notificador(carregar_config(caminho, log=log), log=log)
