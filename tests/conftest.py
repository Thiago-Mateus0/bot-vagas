import os
from pathlib import Path

# Deve rodar ANTES de importar bot_vagas: num clone sem o perfil.yaml real (dados pessoais fora do git),
# os testes usam perfil.example.yaml. Em uso normal do bot a variável não existe.
_RAIZ = Path(__file__).resolve().parents[1]
if not (_RAIZ / "perfil.yaml").exists():
    os.environ.setdefault("BOT_VAGAS_PERFIL", str(_RAIZ / "perfil.example.yaml"))

import pytest  # noqa: E402

from bot_vagas.perfil import PERFIL_PADRAO, carregar_perfil  # noqa: E402


@pytest.fixture(scope="session")
def perfil():
    return carregar_perfil(PERFIL_PADRAO)


@pytest.fixture(autouse=True)
def _isolar_arquivos_reais(tmp_path_factory, monkeypatch):
    """Nenhum teste toca no arquivo PARAR real, na pasta de logs/perfil do navegador reais nem no
    notificacao.yaml real (que poderia mandar push de verdade)."""
    base = tmp_path_factory.mktemp("isolado")
    monkeypatch.setenv("BOT_VAGAS_PARAR", str(base / "PARAR"))
    monkeypatch.setattr("bot_vagas.copiloto.PASTA_LOGS_PADRAO", base / "logs")
    monkeypatch.setattr("bot_vagas.copiloto.PASTA_PERFIL_PADRAO", base / "navegador")
    monkeypatch.setattr("bot_vagas.notificacao.NOTIFICACAO_PADRAO", base / "notificacao.yaml")

    def _sem_rede(*a, **k):
        raise RuntimeError("rede proibida nos testes")

    monkeypatch.setattr("bot_vagas.notificacao._enviar_http", _sem_rede)
    yield base
