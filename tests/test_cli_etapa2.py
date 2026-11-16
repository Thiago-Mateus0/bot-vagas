import json
from pathlib import Path

import pytest

from bot_vagas import cli
from bot_vagas.banco import Banco
from bot_vagas.fontes.http import ClienteHTTPFalso
from bot_vagas.pipeline import buscar

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture
def banco_populado(tmp_path):
    from bot_vagas.fontes.greenhouse import Greenhouse
    from bot_vagas.fontes.lever import Lever

    gh, lv = Greenhouse("exemploco"), Lever("exemplolever")
    http = ClienteHTTPFalso({
        gh.url: json.loads((FIX / "greenhouse_exemplo.json").read_text(encoding="utf-8")),
        lv.url: json.loads((FIX / "lever_exemplo.json").read_text(encoding="utf-8")),
    })
    caminho = tmp_path / "v.db"
    with Banco(caminho) as banco:
        total = buscar([gh, lv], http, banco, log=lambda *a, **k: None)
    assert total == {"baixadas": 6, "novas": 6, "fontes_com_erro": 0, "alarmes": 0}
    return caminho


def test_avaliar_sem_real_recusa_e_nao_chama_cliente(banco_populado, capsys):
    chamadas = []

    def fabrica():
        chamadas.append(1)
        raise AssertionError("cliente não deveria ser criado sem --real")

    assert cli.main(["filtrar", "--banco", str(banco_populado)]) == 0
    codigo = cli.main(["avaliar", "--banco", str(banco_populado)], client_factory=fabrica)
    err = capsys.readouterr().err
    assert codigo == 2
    assert "RECUSADO" in err and "--real" in err
    assert chamadas == []
    with Banco(banco_populado) as banco:
        assert banco.resumo_gasto()["chamadas"] == 0
        assert "aprovada" not in banco.contar_por_status()


def test_filtrar_e_listar(banco_populado, capsys):
    cli.main(["filtrar", "--banco", str(banco_populado)])
    out = capsys.readouterr().out
    # estágio backend remoto (GH) + estágio presencial em Maceió (Lever).
    # AJUSTE: "Junior Backend Developer" (Lever) passou a ser bloqueada (júnior excluído por ora).
    assert "2 passaram, 4 bloqueadas" in out

    assert cli.main(["listar", "--banco", str(banco_populado)]) == 0
    out = capsys.readouterr().out
    assert "EMPRESA" in out and "CARGO" in out and "NOTA" in out and "STATUS" in out and "LINK" in out
    assert "https://boards.greenhouse.io/exemploco/jobs/4001" in out
    assert "bloqueada: 4" in out and "nova: 2" in out


def test_sem_subcomando_continua_sendo_curriculo():
    parser = cli.construir_parser()
    args = parser.parse_args(["curriculo", "--idioma", "en"])
    assert args.func is cli.cmd_curriculo and args.idioma == "en"
    assert cli.SUBCOMANDOS[0] == "curriculo"


def test_codigo_nao_le_api_key():
    src = Path(cli.__file__).parent
    for arq in src.rglob("*.py"):
        assert "ANTHROPIC_API_KEY" not in arq.read_text(encoding="utf-8").replace(
            "este código não lê ANTHROPIC_API_KEY", ""), arq
