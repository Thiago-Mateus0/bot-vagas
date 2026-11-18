"""Gera (NÃO instala) os arquivos de `systemd --user` para a rodada diária às 14h.

`bot-vagas gerar-timer` escreve bot-vagas.service e bot-vagas.timer em systemd/ (ou --saida). Instalar é com
o Thiago (ver README): copiar para ~/.config/systemd/user/ e `systemctl --user enable --now bot-vagas.timer`.

A rodada (`bot-vagas rodar`) busca e filtra. Nada é enviado: candidatar é com o copiloto, e quem clica é você.
"""

from __future__ import annotations

from pathlib import Path

from .perfil import RAIZ_PROJETO

HORARIO_PADRAO = "14:00"
NOME = "bot-vagas"


def _caminho_systemd(caminho: Path) -> str:
    """Usa %h (home do usuário, expandido pelo systemd) quando o projeto está dentro do home, para os arquivos
    servirem em qualquer clone em ~/bot-vagas sem conter o nome de usuário."""
    caminho = caminho.resolve()
    home = Path.home().resolve()
    try:
        rel = caminho.relative_to(home)
    except ValueError:
        texto = str(caminho)
    else:
        texto = f"%h/{rel.as_posix()}" if str(rel) != "." else "%h"
    if any(c.isspace() for c in texto):
        raise ValueError(f"caminho com espaço não é suportado pelo gerador: {texto!r}")
    return texto


def conteudo_service(raiz: Path = RAIZ_PROJETO) -> str:
    r = _caminho_systemd(raiz)
    return f"""[Unit]
Description=bot-vagas: rodada diaria (busca + filtro; nada e enviado)

[Service]
Type=oneshot
WorkingDirectory={r}
ExecStart={r}/.venv/bin/python -m bot_vagas.cli rodar
# a rodada termina sozinha (com pausas de alguns minutos entre candidaturas); isto so evita ficar presa
TimeoutStartSec=4h
Nice=10
NoNewPrivileges=yes
"""


def conteudo_timer(horario: str = HORARIO_PADRAO) -> str:
    hh, _, mm = horario.partition(":")
    if not (hh.isdigit() and mm.isdigit() and 0 <= int(hh) <= 23 and 0 <= int(mm) <= 59):
        raise ValueError(f"horário inválido: {horario!r} (use HH:MM)")
    return f"""[Unit]
Description=bot-vagas: rodada diaria as {int(hh):02d}:{int(mm):02d}

[Timer]
OnCalendar=*-*-* {int(hh):02d}:{int(mm):02d}:00
# se o PC estava desligado/suspenso as {int(hh):02d}:{int(mm):02d}, roda assim que voltar
Persistent=true
Unit={NOME}.service

[Install]
WantedBy=timers.target
"""


def gerar(saida: Path | str = RAIZ_PROJETO / "systemd", raiz: Path = RAIZ_PROJETO,
          horario: str = HORARIO_PADRAO) -> list[Path]:
    """Escreve os dois arquivos em `saida` e devolve os caminhos. Não chama systemctl."""
    saida = Path(saida)
    saida.mkdir(parents=True, exist_ok=True)
    service = saida / f"{NOME}.service"
    timer = saida / f"{NOME}.timer"
    service.write_text(conteudo_service(raiz), encoding="utf-8")
    timer.write_text(conteudo_timer(horario), encoding="utf-8")
    return [service, timer]


INSTRUCOES = """Arquivos gerados (NADA foi instalado). Para instalar no SEU PC:
  mkdir -p ~/.config/systemd/user
  cp {service} {timer} ~/.config/systemd/user/
  systemctl --user daemon-reload
  systemctl --user enable --now bot-vagas.timer
  systemctl --user list-timers bot-vagas.timer     # confere o próximo horário
Ver o resultado da última rodada: journalctl --user -u bot-vagas.service -n 50
Desinstalar: systemctl --user disable --now bot-vagas.timer"""
