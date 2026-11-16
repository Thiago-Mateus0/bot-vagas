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
