"""Botão PARAR: enquanto o arquivo `PARAR` existir (na raiz do projeto), nenhuma execução do bot começa e
as que estão em andamento param no próximo ponto seguro (entre fontes, entre vagas, a cada ciclo do copiloto).

Como acionar:
- `bot-vagas parar [motivo]`  (cria o arquivo)  /  `bot-vagas retomar`  (apaga o arquivo);
- botão "Parar tudo" no painel local;
- ou à mão: `touch PARAR` / `rm PARAR`.

O caminho pode ser trocado pela variável BOT_VAGAS_PARAR (usada nos testes para nunca tocar no arquivo real).
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

from .perfil import RAIZ_PROJETO

ARQUIVO_PARAR_PADRAO = RAIZ_PROJETO / "PARAR"
CODIGO_SAIDA_PARADO = 4

MENSAGEM_PARADO = ("PARADO: existe o arquivo {caminho}. Nada será executado enquanto ele existir. "
                   "Para liberar: bot-vagas retomar (ou apague o arquivo / use o painel).")


class ParadaSolicitada(RuntimeError):
    """Levantada nos laços longos quando o arquivo PARAR aparece no meio da execução."""

    def __init__(self, caminho: Path | None = None):
        self.caminho = caminho or caminho_parar()
        super().__init__(MENSAGEM_PARADO.format(caminho=self.caminho))


def caminho_parar() -> Path:
    return Path(os.environ.get("BOT_VAGAS_PARAR") or ARQUIVO_PARAR_PADRAO)


def parar_solicitado() -> bool:
    return caminho_parar().exists()


def exigir_nao_parado() -> None:
    if parar_solicitado():
        raise ParadaSolicitada()


def solicitar_parada(motivo: str = "") -> Path:
    """Cria o arquivo PARAR (idempotente). O conteúdo é só data + motivo curto, para referência."""
    caminho = caminho_parar()
    caminho.parent.mkdir(parents=True, exist_ok=True)
    quando = datetime.now(timezone.utc).isoformat(timespec="seconds")
    motivo = " ".join((motivo or "").split())[:200]
    caminho.write_text(f"parada solicitada em {quando}\n{motivo}\n".rstrip() + "\n", encoding="utf-8")
    return caminho


def retomar() -> bool:
    """Apaga o arquivo PARAR. Devolve True se ele existia."""
    caminho = caminho_parar()
    try:
        caminho.unlink()
        return True
    except FileNotFoundError:
        return False
