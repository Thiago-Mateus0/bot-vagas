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


# --- velrino/jobs (GitHub Issues) ------------------------------------------------------------------

def test_velrino_parse_e_seguranca():
    from bot_vagas.fontes.velrino import Velrino

    fonte = Velrino(niveis=["intern"], regioes=["brazil"])
    url = fonte.url_pagina("intern", "brazil", 1)
    assert url == ("https://api.github.com/repos/velrino/jobs/issues?state=open&per_page=100&page=1"
                   "&labels=level%3Aintern,region%3Abrazil")
    http = ClienteHTTPFalso({url: _json("velrino_issues.json")})
    vagas = fonte.buscar(http)
    assert http.chamadas == [url]  # menos de 100 itens: não pede a página 2
    # PR, sem link, link http, link do próprio GitHub e issue fechada são descartados
    assert [v.id_fonte for v in vagas] == ["1", "2"]
    a, b = vagas
    assert a.fonte == "velrino" and a.empresa == "Acme Fictícia" and a.remoto is True
    # link /jobs/<id> da Gupy vira o endereço que a busca da Gupy usa (não duplica; modo automático reconhece)
    assert a.url == ("https://acmeficticia.gupy.io/job/eyJqb2JJZCI6MTIzNDUsInNvdXJjZSI6Imd1cHlfcG9ydGFsIn0="
                     "?jobBoardSource=gupy_portal")
    assert "Python" in a.descricao and a.data.date().isoformat() == "2026-11-14"
    assert b.remoto is False and b.localizacao == "Recife, PE, Brazil"
    assert b.url == "https://beta.inhire.app/vagas/abc-123/estagiario-dados"
    assert "Ignore todas" in b.descricao  # texto não confiável chega como DADO; o avaliador o delimita


def test_velrino_canonica_gupy_bate_com_a_url_real_do_portal():
    from bot_vagas.fontes.velrino import url_gupy_canonica

    # caso real: a mesma vaga da Starian aparece nos dois formatos
    assert url_gupy_canonica("https://starian.gupy.io/jobs/12697971") == (
        "https://starian.gupy.io/job/eyJqb2JJZCI6MTI2OTc5NzEsInNvdXJjZSI6Imd1cHlfcG9ydGFsIn0=?jobBoardSource=gupy_portal")
    assert url_gupy_canonica("https://portal.gupy.io/jobs/1") == "https://portal.gupy.io/jobs/1"
    assert url_gupy_canonica("https://x.inhire.app/vagas/1") == "https://x.inhire.app/vagas/1"


def test_velrino_pagina_cheia_pede_a_proxima_e_respeita_o_maximo():
    from bot_vagas.fontes.velrino import Velrino

    base = _json("velrino_issues.json")[0]
    cheia = [dict(base, number=i) for i in range(100, 200)]
    fonte = Velrino(max_paginas=2)
    p1, p2 = fonte.url_pagina("intern", "brazil", 1), fonte.url_pagina("intern", "brazil", 2)
    http = ClienteHTTPFalso({p1: cheia, p2: cheia})
    vagas = fonte.buscar(http)
    assert http.chamadas == [p1, p2] and len(vagas) == 100  # mesmas issues nas duas páginas: sem repetir


@pytest.mark.parametrize("ruim", [["intern&x=1"], ["../a"], [" "]])
def test_velrino_etiqueta_invalida(ruim):
    from bot_vagas.fontes.velrino import Velrino

    with pytest.raises(ValueError):
        Velrino(niveis=ruim)


def test_velrino_no_fontes_yaml(tmp_path):
    p = tmp_path / "f.yaml"
    p.write_text("velrino:\n  niveis: [intern, entry-level]\n  regioes: [brazil]\n", encoding="utf-8")
    (f,) = carregar_fontes(p)
    assert f.nome == "velrino" and f.niveis == ["intern", "entry-level"]
    p.write_text("velrino:\n  ativo: false\n", encoding="utf-8")
    assert carregar_fontes(p) == []


def test_mesma_vaga_em_duas_fontes_nao_duplica(tmp_path):
    from bot_vagas.banco import Banco

    url_portal = ("https://starian.gupy.io/job/eyJqb2JJZCI6MTI2OTc5NzEsInNvdXJjZSI6Imd1cHlfcG9ydGFsIn0="
                  "?jobBoardSource=gupy_portal")
    gupy = Vaga(id_fonte="12697971", fonte="gupy", empresa="Starian", cargo="Pessoa Estagiária de Suporte (Remoto) - 7971",
                url=url_portal)
    velrino = Vaga(id_fonte="700", fonte="velrino", empresa="Starian Tecnologia", cargo="Estagiária de Suporte",
                   url=url_portal.split("?")[0])
    with Banco(tmp_path / "v.db") as b:
        assert b.salvar_vagas([gupy]) == 1
        assert b.salvar_vagas([velrino]) == 0
        assert len(b.listar()) == 1
