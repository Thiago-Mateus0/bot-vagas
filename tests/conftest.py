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
def _isolar_arquivos_locais(tmp_path_factory, monkeypatch):
    """Nenhum teste toca o PARAR do projeto, o notificacao.yaml/automacao.yaml reais (nada de ntfy nem de
    candidatura de verdade) nem a pasta real de logs do copiloto em ~/.local/share/bot-vagas."""
    base = tmp_path_factory.mktemp("isolado")
    monkeypatch.setenv("BOT_VAGAS_PARAR", str(base / "PARAR"))
    monkeypatch.setenv("BOT_VAGAS_NOTIFICACAO", str(base / "notificacao-inexistente.yaml"))
    monkeypatch.setenv("BOT_VAGAS_LOGS", str(base / "logs"))
    return base
