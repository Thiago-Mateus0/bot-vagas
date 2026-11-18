"""Botão de parada: o arquivo `PARAR` na raiz do projeto interrompe QUALQUER execução do bot.

- Criar o arquivo (à mão, com `bot-vagas parar` ou pelo botão do painel) pede a parada.
- Comandos que trabalham (buscar, filtrar, avaliar, preparar, candidatar, login-gupy, rodar) se recusam a
  começar enquanto ele existir, e os laços longos verificam o arquivo a cada item/ciclo e param no meio.
- Apagar o arquivo (`bot-vagas retomar` ou o botão do painel) libera de novo.

O arquivo só guarda quando e de onde veio o pedido; nenhum dado pessoal.
O caminho pode ser trocado pela variável BOT_VAGAS_PARAR (os testes usam isso para não tocar o projeto).
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

from .perfil import RAIZ_PROJETO

ARQUIVO_PARAR_PADRAO = RAIZ_PROJETO / "PARAR"
MENSAGEM_PARADA = ("PARADO: existe o arquivo {caminho} (botão PARAR). Nada mais será feito nesta execução. "
                   "Para liberar: bot-vagas retomar (ou apague o arquivo).")


class ParadaPedida(RuntimeError):
    """Levantada no meio de um laço quando o arquivo PARAR aparece."""

    def __init__(self, caminho: Path | None = None):
        self.caminho = caminho or caminho_parar()
        super().__init__(MENSAGEM_PARADA.format(caminho=self.caminho))


def caminho_parar(caminho: Path | str | None = None) -> Path:
    if caminho is not None:
        return Path(caminho)
    env = os.environ.get("BOT_VAGAS_PARAR")
    return Path(env) if env else ARQUIVO_PARAR_PADRAO


def parada_pedida(caminho: Path | str | None = None) -> bool:
    return caminho_parar(caminho).exists()


def verificar(caminho: Path | str | None = None) -> None:
    """Chame entre itens de qualquer laço: levanta ParadaPedida se o arquivo PARAR existir."""
    c = caminho_parar(caminho)
    if c.exists():
        raise ParadaPedida(c)


def pedir_parada(origem: str = "cli", caminho: Path | str | None = None) -> Path:
    c = caminho_parar(caminho)
    quando = datetime.now(timezone.utc).isoformat(timespec="seconds")
    c.write_text(f"parada pedida em {quando} (origem: {origem})\n", encoding="utf-8")
    return c


def retomar(caminho: Path | str | None = None) -> bool:
    """Apaga o arquivo PARAR. Devolve True se ele existia."""
    c = caminho_parar(caminho)
    try:
        c.unlink()
    except FileNotFoundError:
        return False
    return True
