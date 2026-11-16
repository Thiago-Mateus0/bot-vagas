import json
from datetime import datetime
from pathlib import Path

import pytest

from bot_vagas.fontes import Vaga
from bot_vagas.fontes.config import carregar_fontes
from bot_vagas.fontes.greenhouse import Greenhouse
from bot_vagas.fontes.gupy import Gupy
from bot_vagas.fontes.http import USER_AGENT, ClienteHTTPFalso, ErroHTTP
from bot_vagas.fontes.lever import Lever
from bot_vagas.fontes.texto import html_para_texto, normalizar

FIX = Path(__file__).parent / "fixtures"


def _json(nome):
    return json.loads((FIX / nome).read_text(encoding="utf-8"))


def test_greenhouse_parse():
    fonte = Greenhouse("exemploco")
    http = ClienteHTTPFalso({fonte.url: _json("greenhouse_exemplo.json")})
    vagas = fonte.buscar(http)

    assert http.chamadas == ["https://boards-api.greenhouse.io/v1/boards/exemploco/jobs?content=true"]
    assert len(vagas) == 3  # a 4ª não tem título e é descartada
    v = vagas[0]
    assert isinstance(v, Vaga)
    assert v.fonte == "greenhouse" and v.id_fonte == "4001"
    assert v.empresa == "ExemploCo"
    assert v.cargo == "Estágio em Desenvolvimento Backend (Remoto)"
    assert v.localizacao == "Remote - Brazil" and v.remoto is True
    assert "<" not in v.descricao and "PostgreSQL" in v.descricao and "estagiário" in v.descricao
    assert v.url.startswith("https://boards.greenhouse.io/exemploco/jobs/4001")
    assert isinstance(v.data, datetime)
    assert vagas[2].empresa == "exemploco"  # sem company_name cai no token
    assert vagas[1].remoto is None


def test_lever_parse():
    fonte = Lever("exemplolever")
    http = ClienteHTTPFalso({fonte.url: _json("lever_exemplo.json")})
    vagas = fonte.buscar(http)

    assert http.chamadas == ["https://api.lever.co/v0/postings/exemplolever?mode=json"]
    assert len(vagas) == 3
    v = vagas[0]
    assert v.fonte == "lever" and v.empresa == "exemplolever"
    assert v.cargo == "Junior Backend Developer" and v.remoto is True
    assert "FastAPI" in v.descricao and "Entry level" in v.descricao and "<li>" not in v.descricao
    assert v.data.year == 2025
    assert vagas[1].remoto is False and vagas[2].remoto is False


def test_fontes_rejeitam_token_com_barra():
    with pytest.raises(ValueError):
        Greenhouse("a/b")
    with pytest.raises(ValueError):
        Lever("../x")


def test_cliente_falso_sem_resposta_gera_erro():
    with pytest.raises(ErroHTTP):
        Greenhouse("x").buscar(ClienteHTTPFalso({}))


def test_user_agent_generico_sem_dados_pessoais():
    assert USER_AGENT == "bot-vagas/0.1"
    assert "@" not in USER_AGENT and "thiago" not in USER_AGENT.lower()


def test_cliente_real_recusa_http_sem_tls():
    from bot_vagas.fontes.http import ClienteHTTPReal

    with pytest.raises(ErroHTTP):
        ClienteHTTPReal().get_json("http://example.com/x")


def test_gupy_implementada_e_sem_termos_nao_entra():
    assert Gupy.implementada is True
    assert Gupy([]).buscar(ClienteHTTPFalso({})) == []


def test_carregar_fontes_yaml(tmp_path):
    arq = tmp_path / "fontes.yaml"
    arq.write_text("greenhouse:\n  - token: a\nlever:\n  - empresa: b\ngupy: []\n", encoding="utf-8")
    fontes = carregar_fontes(arq)
    assert [f.nome for f in fontes] == ["greenhouse", "lever"]


def test_carregar_fontes_yaml_com_gupy(tmp_path):
    arq = tmp_path / "fontes.yaml"
    arq.write_text(
        "gupy:\n  max_paginas_por_termo: 2\n  max_requisicoes: 7\n  pausa_segundos: 0.5\n"
        "  termos: [estagio ti, estagio maceio]\n",
        encoding="utf-8",
    )
    (g,) = carregar_fontes(arq)
    assert isinstance(g, Gupy) and g.termos == ["estagio ti", "estagio maceio"]
    assert g.max_paginas_por_termo == 2 and g.max_requisicoes == 7
    assert g.pausa_segundos == 2.0  # mínimo imposto pelo código


def test_fontes_yaml_do_projeto_carrega():
    from bot_vagas.fontes.config import FONTES_PADRAO

    assert carregar_fontes(FONTES_PADRAO)


def test_texto():
    assert html_para_texto("<p>a&amp;b</p><ul><li>c</li></ul>") == "a&b\nc"
    assert normalizar("  Estágio  JÚNIOR ") == "estagio junior"
