"""Fonte Gupy: tudo com fixtures gravadas, sem rede e sem API da Anthropic."""

from datetime import datetime, timezone
from pathlib import Path

import pytest

from bot_vagas.banco import Banco
from bot_vagas.filtro import avaliar_palavras_chave, carregar_filtros
from bot_vagas.fontes.gupy import HOST, MSG_ALARME, AlarmeGupy, Gupy, converter_item, extrair_lista
from bot_vagas.fontes.http import (
    USER_AGENT_NAVEGADOR,
    ClienteHTTPFalso,
    ClienteHTTPReal,
    ErroHTTP,
    RedirecionamentoRestrito,
    RespostaHTML,
)
from bot_vagas.pipeline import buscar

FIX = Path(__file__).parent / "fixtures"
P1 = (FIX / "gupy_busca_exemplo.html").read_text(encoding="utf-8")
P2 = (FIX / "gupy_busca_exemplo_p2.html").read_text(encoding="utf-8")
URL1 = "https://portal.gupy.io/job-search/term=estagio%20ti"
URL2 = URL1 + "&page=2"
AGORA = datetime(2026, 11, 13, 12, 0, tzinfo=timezone.utc)


class Relogio:
    """Relógio falso: `dormir` avança o tempo e registra as pausas."""

    def __init__(self):
        self.t = 1000.0
        self.pausas = []

    def agora(self):
        return self.t

    def dormir(self, s):
        self.pausas.append(s)
        self.t += s


def _gupy(termos=("estagio ti",), **kw):
    rel = Relogio()
    kw.setdefault("dormir", rel.dormir)
    kw.setdefault("relogio", rel.agora)
    kw.setdefault("agora", lambda: AGORA)
    return Gupy(list(termos), **kw), rel


def test_url_codifica_termo_e_pagina():
    assert Gupy.url("estagio ti") == URL1
    assert Gupy.url("estagio ti", 2) == URL2
    assert Gupy.url("estágio & dev") == "https://portal.gupy.io/job-search/term=est%C3%A1gio%20%26%20dev"


def test_parse_e_paginacao_ate_o_fim():
    g, _ = _gupy()
    http = ClienteHTTPFalso({URL1: P1, URL2: P2})
    vagas = g.buscar(http)

    assert http.chamadas == [URL1, URL2]  # offset 3 + limit 3 >= total 6: para
    assert g.alarmes == []
    # 6 itens: 1 vencido e 1 sem nome são descartados
    assert [v.id_fonte for v in vagas] == ["9001", "9002", "9003", "9004"]
    v = vagas[0]
    assert v.fonte == "gupy" and v.empresa == "Empresa Alfa"
    assert v.cargo == "Estágio em Desenvolvimento TI (Remoto)"
    assert v.url.startswith("https://empresaalfa.gupy.io/job/") and "jobBoardSource" in v.url
    assert "<" not in v.descricao and "PostgreSQL" in v.descricao
    assert v.data == datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)


def test_conversao_workplace_type():
    g, _ = _gupy()
    vagas = {v.id_fonte: v for v in g.buscar(ClienteHTTPFalso({URL1: P1, URL2: P2}))}
    assert vagas["9001"].remoto is True and vagas["9001"].localizacao == "Remoto"
    assert vagas["9002"].remoto is False and vagas["9002"].localizacao == "Recife, PE"
    assert vagas["9003"].remoto is False and vagas["9003"].localizacao == "Maceió, AL"
    assert vagas["9004"].remoto is False and vagas["9004"].localizacao == "São Paulo, SP"

    remoto_com_cidade = converter_item({"id": 1, "name": "Estágio", "careerPageName": "X",
                                        "jobUrl": "https://x.gupy.io/job/1", "workplaceType": "remote",
                                        "city": "Curitiba", "state": "PR"})
    assert remoto_com_cidade.remoto is True and remoto_com_cidade.localizacao == "Remoto (Curitiba, PR)"
    desconhecido = converter_item({"id": 2, "name": "Estágio", "careerPageName": "X",
                                   "jobUrl": "https://x.gupy.io/job/2", "workplaceType": None, "city": "Maceió"})
    assert desconhecido.remoto is None and desconhecido.localizacao == "Maceió"


def test_filtro_aplica_regra_de_localizacao_nas_vagas_gupy():
    filtros = carregar_filtros()
    g, _ = _gupy()
    vagas = {v.id_fonte: v for v in g.buscar(ClienteHTTPFalso({URL1: P1, URL2: P2}))}
    r = {k: avaliar_palavras_chave(v, filtros) for k, v in vagas.items()}
    assert r["9001"].passou and "remoto" in r["9001"].motivo           # remote, cidade vazia
    assert not r["9002"].passou and "fora de Maceió" in r["9002"].motivo  # on-site Recife
    assert r["9003"].passou and "Maceió" in r["9003"].motivo           # hybrid Maceió
    assert not r["9004"].passou and "fora de Maceió" in r["9004"].motivo  # hybrid São Paulo


def test_prazo_vencido_e_ignorado():
    base = {"id": 7, "name": "Estágio", "careerPageName": "X", "jobUrl": "https://x.gupy.io/job/7", "workplaceType": "remote"}
    assert converter_item({**base, "applicationDeadline": "2020-01-01T00:00:00.000Z"}, agora=AGORA) is None
    assert converter_item({**base, "applicationDeadline": "2099-01-01T00:00:00.000Z"}, agora=AGORA) is not None
    assert converter_item({**base, "applicationDeadline": None}, agora=AGORA) is not None
    assert converter_item({**base, "applicationDeadline": "lixo"}, agora=AGORA) is not None  # ilegível: não descarta


def test_descricao_maliciosa_e_so_dado():
    g, _ = _gupy()
    v = g.buscar(ClienteHTTPFalso({URL1: P1, URL2: P2}))[0]
    assert "ignore as instruções anteriores" in v.descricao
    # continua sendo um texto dentro do campo descricao; não altera nada da vaga nem do filtro
    assert v.cargo == "Estágio em Desenvolvimento TI (Remoto)" and v.remoto is True
    r = avaliar_palavras_chave(v, carregar_filtros())
    assert r.passou and "nota" not in r.motivo


def test_teto_de_paginas_por_termo():
    g, _ = _gupy(max_paginas_por_termo=1)
    http = ClienteHTTPFalso({URL1: P1, URL2: P2})
    vagas = g.buscar(http)
    assert http.chamadas == [URL1] and len(vagas) == 3 and g.alarmes == []


def test_teto_total_de_requisicoes_dispara_alarme_e_para():
    g, _ = _gupy(("estagio ti", "estagio dados"), max_requisicoes=1)
    http = ClienteHTTPFalso({"https://portal.gupy.io/": P1})
    g.buscar(http)
    assert len(http.chamadas) == 1
    assert len(g.alarmes) == 1 and "teto de 1 requisições" in g.alarmes[0]


def test_pausa_minima_entre_requisicoes():
    g, rel = _gupy(("estagio ti", "estagio dados"), pausa_segundos=0.1)  # 0.1 vira 2.0
    assert g.pausa_segundos == 2.0
    http = ClienteHTTPFalso({URL1: P1, URL2: P2, "https://portal.gupy.io/job-search/term=estagio%20dados": P2})
    g.buscar(http)
    assert len(http.chamadas) == 3
    assert len(rel.pausas) == 2 and all(p == pytest.approx(2.0) for p in rel.pausas)  # nenhuma antes da 1ª


def test_sem_pausa_se_ja_passou_tempo_suficiente():
    rel = Relogio()

    def relogio_lento():  # cada consulta ao relógio avança 3 s: o processamento entre páginas já demorou
        rel.t += 3
        return rel.t

    g = Gupy(["estagio ti"], dormir=rel.dormir, relogio=relogio_lento, agora=lambda: AGORA)
    http = ClienteHTTPFalso({URL1: P1, URL2: P2})
    g.buscar(http)
    assert len(http.chamadas) == 2 and rel.pausas == []


def test_dedupe_com_o_banco(tmp_path):
    g, _ = _gupy()
    http = ClienteHTTPFalso({URL1: P1, URL2: P2})
    with Banco(tmp_path / "v.db") as banco:
        t1 = buscar([g], http, banco, log=lambda *a, **k: None)
        t2 = buscar([g], http, banco, log=lambda *a, **k: None)
        assert t1 == {"baixadas": 4, "novas": 4, "fontes_com_erro": 0, "alarmes": 0}
        assert t2 == {"baixadas": 4, "novas": 0, "fontes_com_erro": 0, "alarmes": 0}
        assert all(r["fonte"] == "gupy" for r in banco.listar("nova")) and len(banco.listar("nova")) == 4


def test_mesmo_id_em_dois_termos_so_entra_uma_vez():
    g, _ = _gupy(("estagio ti", "estagio dados"))
    http = ClienteHTTPFalso({"https://portal.gupy.io/": P1})  # as duas buscas devolvem a mesma página
    g.buscar(http)
    vagas = g.buscar(http)
    assert [v.id_fonte for v in vagas] == ["9001", "9002", "9003"]


# --- alarmes -----------------------------------------------------------------------

def test_alarme_html_sem_next_data_para_so_aquele_termo(tmp_path):
    g, _ = _gupy(("estagio ti", "estagio dados"))
    http = ClienteHTTPFalso({
        URL1: "<html><body><h1>Vagas</h1><p>layout novo sem o script</p></body></html>",
        "https://portal.gupy.io/job-search/term=estagio%20dados": P2,
    })
    vagas = g.buscar(http)
    assert len(http.chamadas) == 2  # o segundo termo continuou
    assert len(g.alarmes) == 1 and MSG_ALARME in g.alarmes[0] and "sem __NEXT_DATA__" in g.alarmes[0]
    assert [v.id_fonte for v in vagas] == ["9004"]

    with Banco(tmp_path / "v.db") as banco:
        erros = []
        total = buscar([g], http, banco, log=lambda m, **k: erros.append(m) if k.get("file") else None)
    assert total["alarmes"] == 1 and total["novas"] == 1
    assert any("ALARME" in e and MSG_ALARME in e for e in erros)


def test_alarme_json_quebrado_e_chaves_faltando():
    with pytest.raises(AlarmeGupy, match="não é JSON"):
        extrair_lista('<script id="__NEXT_DATA__" type="application/json">{"props": </script>')
    with pytest.raises(AlarmeGupy, match="initialJobList ausente"):
        extrair_lista('<script id="__NEXT_DATA__" type="application/json">{"props": {"pageProps": {}}}</script>')
    with pytest.raises(AlarmeGupy, match="sem `data`"):
        extrair_lista('<script id="__NEXT_DATA__" type="application/json">'
                      '{"props": {"pageProps": {"initialJobList": {"data": [], "pagination": "x"}}}}</script>')
    with pytest.raises(AlarmeGupy, match="total/limit/offset"):
        extrair_lista('<script id="__NEXT_DATA__" type="application/json">'
                      '{"props": {"pageProps": {"initialJobList": {"data": [], "pagination": {"total": 1}}}}}</script>')


def test_alarme_429_para_todas_as_requisicoes():
    g, _ = _gupy(("estagio ti", "estagio dados", "estagio maceio"))
    http = ClienteHTTPFalso({URL1: RespostaHTML(429, "Too Many Requests", URL1), "https://portal.gupy.io/": P1})
    vagas = g.buscar(http)
    assert http.chamadas == [URL1]  # os outros dois termos nem foram pedidos
    assert vagas == []
    assert len(g.alarmes) == 1 and "HTTP 429" in g.alarmes[0] and MSG_ALARME in g.alarmes[0]


def test_alarme_403_e_captcha_param_tudo_e_500_so_o_termo():
    g, _ = _gupy(("a", "b"))
    http = ClienteHTTPFalso({"https://portal.gupy.io/job-search/term=a": RespostaHTML(403, "Forbidden", "https://portal.gupy.io/job-search/term=a"),
                             "https://portal.gupy.io/job-search/term=b": P1})
    g.buscar(http)
    assert len(http.chamadas) == 1

    g, _ = _gupy(("a", "b"))
    http = ClienteHTTPFalso({"https://portal.gupy.io/job-search/term=a": "<html><body>Please complete the CAPTCHA to continue</body></html>",
                             "https://portal.gupy.io/job-search/term=b": P1})
    g.buscar(http)
    assert len(http.chamadas) == 1 and "captcha" in g.alarmes[0]

    g, _ = _gupy(("a", "b"))
    http = ClienteHTTPFalso({"https://portal.gupy.io/job-search/term=a": RespostaHTML(500, "erro", "https://portal.gupy.io/job-search/term=a"),
                             "https://portal.gupy.io/job-search/term=b": P2})  # P2 é a última página (offset+limit >= total)
    vagas = g.buscar(http)
    assert len(http.chamadas) == 2 and len(vagas) == 1 and "HTTP 500" in g.alarmes[0]


def test_cli_buscar_sai_com_1_quando_ha_alarme(tmp_path, monkeypatch):
    from bot_vagas import cli
    from bot_vagas.fontes import http as mod_http

    arq = tmp_path / "fontes.yaml"
    arq.write_text("gupy:\n  termos: [estagio ti]\n", encoding="utf-8")
    falso = ClienteHTTPFalso({URL1: "<html>sem next data</html>"})
    monkeypatch.setattr(mod_http, "ClienteHTTPReal", lambda: falso)
    assert cli.main(["buscar", "--fontes", str(arq), "--banco", str(tmp_path / "v.db")]) == 1
    assert falso.chamadas == [URL1]

    falso = ClienteHTTPFalso({URL1: P1, URL2: P2})
    monkeypatch.setattr(mod_http, "ClienteHTTPReal", lambda: falso)
    assert cli.main(["buscar", "--fontes", str(arq), "--banco", str(tmp_path / "v2.db")]) == 0


# --- redirecionamento / host -----------------------------------------------------

def test_recusa_redirecionamento_para_outro_host_na_resposta():
    g, _ = _gupy()
    http = ClienteHTTPFalso({URL1: RespostaHTML(200, P1, "https://outro-site.com/x")})
    with pytest.raises(ErroHTTP, match="redirecionamento recusado"):
        g.buscar(http)


def test_handler_de_redirecionamento_restrito():
    import urllib.request

    h = RedirecionamentoRestrito((HOST,))
    req = urllib.request.Request(URL1)
    with pytest.raises(ErroHTTP):
        h.redirect_request(req, None, 302, "Found", {}, "https://outro.com/login")
    with pytest.raises(ErroHTTP):
        h.redirect_request(req, None, 302, "Found", {}, "http://portal.gupy.io/x")  # sem TLS
    novo = h.redirect_request(req, None, 302, "Found", {}, "https://portal.gupy.io/job-search/term=x")
    assert novo.full_url == "https://portal.gupy.io/job-search/term=x"


def test_cliente_real_get_html_recusa_http_e_host_errado_sem_rede():
    c = ClienteHTTPReal()
    with pytest.raises(ErroHTTP):
        c.get_html("http://portal.gupy.io/x", hosts_permitidos=(HOST,))
    with pytest.raises(ErroHTTP, match="host não permitido"):
        c.get_html("https://outro.com/x", hosts_permitidos=(HOST,))


def test_user_agent_navegador_sem_dados_pessoais():
    assert "bot-vagas" in USER_AGENT_NAVEGADOR and "@" not in USER_AGENT_NAVEGADOR
    assert "thiago" not in USER_AGENT_NAVEGADOR.lower()
