"""Gera (NÃO instala) as unidades `systemd --user` da rodada diária às 14h.

`bot-vagas gerar-timer` escreve dois arquivos em saida/systemd/ (por padrão):
- bot-vagas.service: roda `python -m bot_vagas.cli rodar` (buscar + filtrar; nenhuma chamada paga, nada é enviado);
- bot-vagas.timer:   dispara às 14:00 com Persistent=true (se o PC estava desligado/suspenso às 14h, roda ao ligar).

Instalar é com o Thiago (ver README, seção "Agendamento"). Este módulo nunca chama systemctl.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from .perfil import RAIZ_PROJETO

NOME = "bot-vagas"
HORA_PADRAO = "14:00"


def _citar(arg: str) -> str:
    """Aspas no estilo do systemd para ExecStart (caminhos com espaço)."""
    if re.fullmatch(r"[A-Za-z0-9_@%+=:,./-]+", arg):
        return arg
    return '"' + arg.replace("\\", "\\\\").replace('"', '\\"') + '"'


def validar_hora(hora: str) -> str:
    m = re.fullmatch(r"([01]?\d|2[0-3]):([0-5]\d)", hora.strip())
    if not m:
        raise ValueError(f"hora inválida: {hora!r} (use HH:MM, ex.: 14:00)")
    return f"{int(m.group(1)):02d}:{m.group(2)}"


def gerar_unidades(raiz: Path | str = RAIZ_PROJETO, python: Path | str | None = None,
                   hora: str = HORA_PADRAO) -> dict[str, str]:
    raiz = Path(raiz).resolve()
    python = Path(python) if python else raiz / ".venv" / "bin" / "python"
    hora = validar_hora(hora)
    if "\n" in str(raiz) or "\n" in str(python):
        raise ValueError("caminho com quebra de linha não é aceito")
    exec_start = " ".join(_citar(a) for a in (str(python), "-m", "bot_vagas.cli", "rodar"))
    service = f"""[Unit]
Description=bot-vagas: rodada diária (buscar + filtrar; não envia nada, não chama API paga)
Documentation=file://{raiz}/README.md
Wants=network-online.target
After=network-online.target

[Service]
Type=oneshot
WorkingDirectory={raiz}
ExecStart={exec_start}
# Rodada curta; se travar, o systemd encerra (SIGTERM) e o bot sai limpo.
TimeoutStartSec=1h
Nice=10
# Respeita o botão PARAR: se o arquivo {raiz}/PARAR existir, o comando sai sem fazer nada.
"""
    timer = f"""[Unit]
Description=bot-vagas: dispara a rodada diária às {hora}

[Timer]
OnCalendar=*-*-* {hora}:00
# Se o PC estava desligado/suspenso no horário, roda assim que ligar.
Persistent=true
Unit={NOME}.service

[Install]
WantedBy=timers.target
"""
    return {f"{NOME}.service": service, f"{NOME}.timer": timer}


def escrever_unidades(destino: Path | str, **kw) -> list[Path]:
    destino = Path(destino)
    destino.mkdir(parents=True, exist_ok=True)
    saida = []
    for nome, conteudo in gerar_unidades(**kw).items():
        caminho = destino / nome
        caminho.write_text(conteudo, encoding="utf-8")
        saida.append(caminho)
    return saida


INSTRUCOES = """Arquivos gerados (NADA foi instalado). Para instalar no SEU computador:
  mkdir -p ~/.config/systemd/user
  cp {destino}/bot-vagas.service {destino}/bot-vagas.timer ~/.config/systemd/user/
  systemctl --user daemon-reload
  systemctl --user enable --now bot-vagas.timer
  systemctl --user list-timers bot-vagas.timer     # confira o próximo disparo
Para rodar o timer mesmo sem você logado: loginctl enable-linger "$USER"
Para testar agora:   systemctl --user start bot-vagas.service && journalctl --user -u bot-vagas -n 50
Para desinstalar:    systemctl --user disable --now bot-vagas.timer"""


def python_atual() -> str:
    return sys.executable
