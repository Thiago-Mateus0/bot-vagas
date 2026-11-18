"""Aviso no celular via ntfy.sh (OPCIONAL, desligado por padrão).

- Só liga se existir `notificacao.yaml` (local, fora do git) com um `topico` preenchido.
- As mensagens são FIXAS e genéricas (tabela MENSAGENS): nenhum dado pessoal, nome de empresa, cargo, URL,
  valor de formulário ou texto da vaga sai daqui. O único dado variável permitido é um número inteiro
  (ex.: quantas vagas passaram no filtro).
- Qualquer falha de rede é engolida com um aviso curto: notificação nunca derruba o bot.

Atenção: no ntfy.sh público, quem souber o nome do tópico lê as mensagens. Use um nome longo e aleatório.
"""

from __future__ import annotations

import re
import sys
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit

import yaml

from .perfil import RAIZ_PROJETO

NOTIFICACAO_PADRAO = RAIZ_PROJETO / "notificacao.yaml"
SERVIDOR_PADRAO = "https://ntfy.sh"
TIMEOUT_S = 10

# evento -> (título, mensagem). {n} é o ÚNICO campo variável e só aceita inteiro.
MENSAGENS: dict[str, tuple[str, str]] = {
    "login": ("bot-vagas: aguardando você", "O copiloto encontrou uma tela de login e está esperando você entrar."),
    "parado": ("bot-vagas: parado", "Uma execução foi interrompida pelo botão PARAR."),
    "alarme": ("bot-vagas: atenção", "A busca encontrou bloqueio ou mudança de formato em uma fonte e parou essa fonte."),
    "rodada": ("bot-vagas: rodada concluída", "Rodada diária concluída: {n} vaga(s) nova(s) passaram no filtro."),
    "erro": ("bot-vagas: erro", "A rodada diária terminou com erro. Veja o terminal/log no computador."),
    "teste": ("bot-vagas: teste", "Notificação de teste do bot-vagas."),
}

_TOPICO_VALIDO = re.compile(r"^[A-Za-z0-9_-]{6,64}$")


class ConfigNotificacaoInvalida(ValueError):
    pass


@dataclass(frozen=True)
class ConfigNotificacao:
    topico: str | None = None
    servidor: str = SERVIDOR_PADRAO

    @property
    def ligada(self) -> bool:
        return bool(self.topico)


DESLIGADA = ConfigNotificacao()


def carregar_notificacao(caminho: Path | str = NOTIFICACAO_PADRAO) -> ConfigNotificacao:
    caminho = Path(caminho)
    if not caminho.exists():
        return DESLIGADA
    with open(caminho, encoding="utf-8") as f:
        d = yaml.safe_load(f) or {}
    ntfy = d.get("ntfy") if isinstance(d, dict) else None
    if not isinstance(ntfy, dict) or not ntfy.get("topico"):
        return DESLIGADA
    topico = str(ntfy["topico"]).strip()
    servidor = str(ntfy.get("servidor") or SERVIDOR_PADRAO).strip().rstrip("/")
    if not _TOPICO_VALIDO.fullmatch(topico):
        raise ConfigNotificacaoInvalida("tópico do ntfy inválido: use 6 a 64 caracteres entre letras, números, '-' e '_'")
    u = urlsplit(servidor)
    if u.scheme != "https" or not u.hostname or u.path not in ("", "/") or u.query or u.username:
        raise ConfigNotificacaoInvalida("servidor do ntfy deve ser só https://host (sem caminho, usuário ou query)")
    return ConfigNotificacao(topico=topico, servidor=servidor)


def montar_mensagem(evento: str, n: int | None = None) -> tuple[str, str]:
    if evento not in MENSAGENS:
        raise KeyError(f"evento de notificação desconhecido: {evento}")
    titulo, msg = MENSAGENS[evento]
    if "{n}" in msg:
        msg = msg.format(n=int(n or 0))
    return titulo, msg


def _enviar_http(url: str, corpo: bytes, cabecalhos: dict[str, str]) -> None:
    req = urllib.request.Request(url, data=corpo, headers=cabecalhos, method="POST")
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:  # noqa: S310 (https validado na config)
        r.read(64)


Enviar = Callable[[str, bytes, dict[str, str]], None]


class Notificador:
    """`notificar(evento)` manda (no máximo uma vez por evento, por execução, para não inundar o celular)."""

    def __init__(self, config: ConfigNotificacao = DESLIGADA, enviar: Enviar | None = None, log=None):
        self.config = config
        self._enviar = enviar or _enviar_http
        self._log = log or (lambda *a, **k: print(*a, file=sys.stderr, **k))
        self._enviados: set[str] = set()

    def notificar(self, evento: str, n: int | None = None, *, repetir: bool = False) -> bool:
        if not self.config.ligada:
            return False
        if evento in self._enviados and not repetir:
            return False
        titulo, msg = montar_mensagem(evento, n)
        url = f"{self.config.servidor}/{self.config.topico}"
        # cabeçalhos HTTP precisam ser latin-1: o título vai sem acento
        cab = {"Title": titulo.encode("ascii", "ignore").decode(), "Tags": "briefcase", "User-Agent": "bot-vagas/0.1"}
        try:
            self._enviar(url, msg.encode("utf-8"), cab)
        except Exception as e:  # rede fora, DNS, 4xx/5xx: nunca derruba o bot
            self._log(f"(aviso: notificação ntfy não enviada: {type(e).__name__})")
            return False
        self._enviados.add(evento)
        return True


NOTIFICADOR_DESLIGADO = Notificador(DESLIGADA)
