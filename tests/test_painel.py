"""Etapa 3: painel web local somente leitura. Sem rede, sem API real."""

import json
from datetime import datetime, timezone
from unittest import mock

import pytest
from fastapi.testclient import TestClient

from bot_vagas.banco import Banco
from bot_vagas.fontes import Vaga
from bot_vagas.gasto import CUSTOS_PADRAO, carregar_custos
from bot_vagas.painel import criar_app, host_permitido, url_segura

AGORA = datetime(2026, 11, 15, 12, 0, tzinfo=timezone.utc)
TETO = carregar_custos(CUSTOS_PADRAO).teto_mensal_usd


def _vaga(**kw):
    base = dict(id_fonte="1", fonte="lever", empresa="Acme", cargo="Junior Backend", localizacao="Remote",
                remoto=True, descricao="Python, FastAPI.", url="https://jobs.lever.co/acme/1")
    base.update(kw)
    return Vaga(**base)


@pytest.fixture
def banco_path(tmp_path):
    caminho = tmp_path / "t.db"
    with Banco(caminho) as b:
        b.salvar_vagas([
            _vaga(id_fonte="1", empresa="Acme", cargo="Junior Backend"),
            _vaga(id_fonte="2", empresa="Beta", cargo="Estágio Node", url="https://jobs.lever.co/beta/2"),
            _vaga(id_fonte="3", empresa="Gama", cargo="Trainee Java", url="https://jobs.lever.co/gama/3"),
            _vaga(id_fonte="4", empresa="Delta", cargo="Data Scientist", url="https://jobs.lever.co/delta/4"),
            _vaga(id_fonte="5", empresa="<b>Mal</b>", cargo="X <script>alert('xss')</script>",
                  url="javascript:alert(1)", descricao="<img src=x onerror=alert(1)>"),
        ])
        chaves = {r["empresa"]: r["chave"] for r in b.listar()}
        b.atualizar_status(chaves["Acme"], "aprovada", nota=9, motivo="ótimo match", detalhes=json.dumps({
            "filtro": "passou", "pontos": 2, "termos": ["python", "fastapi"],
            "avaliacao": {"nota": 9, "motivo": "ótimo match", "pontos_de_match": ["Python", "FastAPI"],
                          "requisitos_que_faltam": ["Docker"]}}))
        b.atualizar_status(chaves["Beta"], "aprovada", nota=7, motivo="ok")
        b.atualizar_status(chaves["Gama"], "abaixo_do_corte", nota=4, motivo="java")
        b.atualizar_status(chaves["Delta"], "bloqueada", motivo="bloqueio: data scientist <b>")
    return caminho


def _cliente(banco_path, gasto_usd=0.0, n=0):
    with Banco(banco_path) as b:
        for i in range(n):
            b.registrar_chamada(modelo="claude-haiku-5-5", finalidade="avaliar", chave_vaga=None,
                                tokens_entrada=1000, tokens_saida=100, tokens_cache_leitura=0,
                                tokens_cache_escrita=0, custo_usd=gasto_usd / n, quando=f"2026-11-{10 + i:02d}T10:00:00+00:00")
    app = criar_app(banco_path, CUSTOS_PADRAO, agora=lambda: AGORA)
    return TestClient(app, base_url="http://127.0.0.1")


# --- helpers ---------------------------------------------------------------------

def test_url_segura():
    assert url_segura("https://x.com/a") == "https://x.com/a"
    assert url_segura("http://x.com") == "http://x.com"
    assert url_segura("javascript:alert(1)") is None
    assert url_segura("JavaScript:alert(1)") is None
    assert url_segura("data:text/html,oi") is None
    assert url_segura("ftp://x.com") is None
    assert url_segura("/relativo") is None
    assert url_segura("") is None and url_segura(None) is None


def test_host_permitido():
    for ok in ("localhost", "localhost:8765", "127.0.0.1", "127.0.0.1:9000", "[::1]:8765", "LOCALHOST"):
        assert host_permitido(ok), ok
    for ruim in ("evil.com", "127.0.0.1.evil.com", "localhost.evil.com", "0.0.0.0", "192.168.0.10:8765", "", None):
        assert not host_permitido(ruim), ruim


# --- lista e filtros -------------------------------------------------------------

def test_lista_mostra_vagas_e_contagem(banco_path):
    c = _cliente(banco_path)
    r = c.get("/")
    assert r.status_code == 200
    html = r.text
    for s in ("Acme", "Junior Backend", "Beta", "Gama", "Delta", "lever"):
        assert s in html
    assert "Aprovadas" in html and "Bloqueadas" in html and "Abaixo do corte" in html and "Novas" in html
    assert 'rel="noopener noreferrer"' in html
    # ordenação por nota: Acme (9) antes de Beta (7) antes de Gama (4)
    assert html.index("Acme") < html.index("Beta") < html.index("Gama")


def test_filtro_por_status(banco_path):
    c = _cliente(banco_path)
    html = c.get("/", params={"status": "aprovada"}).text
    assert "Acme" in html and "Beta" in html
    assert "Gama" not in html and "Delta" not in html
    # status inválido é ignorado, não dá erro
    assert c.get("/", params={"status": "'; DROP TABLE vagas; --"}).status_code == 200


def test_filtro_por_nota_minima(banco_path):
    c = _cliente(banco_path)
    html = c.get("/", params={"nota_min": "7"}).text
    assert "Acme" in html and "Beta" in html
    assert "Gama" not in html and "Delta" not in html  # Delta sem nota some também
    assert c.get("/", params={"nota_min": "abc"}).status_code == 200


def test_ordenacao_por_data(banco_path):
    c = _cliente(banco_path)
    assert c.get("/", params={"ordem": "data"}).status_code == 200
    assert c.get("/", params={"ordem": "qualquer"}).status_code == 200


# --- detalhe ---------------------------------------------------------------------

def test_detalhe(banco_path):
    with Banco(banco_path) as b:
        chave = next(r["chave"] for r in b.listar() if r["empresa"] == "Acme")
    c = _cliente(banco_path)
    r = c.get(f"/vaga/{chave}")
    assert r.status_code == 200
    html = r.text
    assert "Python, FastAPI." in html
    assert "Pontos de match" in html and "Docker" in html and "Requisitos que faltam" in html
    assert "Histórico de status" in html and "Vaga encontrada" in html and "Filtro de palavras-chave: passou" in html
    assert "Avaliação do modelo: nota 9" in html and "Status atual: Aprovada" in html
    assert 'href="https://jobs.lever.co/acme/1"' in html


def test_detalhe_inexistente(banco_path):
    assert _cliente(banco_path).get("/vaga/naoexiste").status_code == 404


# --- gasto -----------------------------------------------------------------------

def test_gasto_sem_aviso(banco_path):
    c = _cliente(banco_path, gasto_usd=TETO * 0.5, n=4)
    html = c.get("/gasto").text
    assert "<progress" in html and 'max="100"' in html and 'value="50.0"' in html
    assert "Teto atingido" not in html and "80% ou mais" not in html
    assert "Últimas chamadas" in html and html.count("<tr>") >= 5  # cabeçalho + 4 chamadas
    assert "4.000" in html  # tokens de entrada somados (1000 x 4)


def test_gasto_aviso_80(banco_path):
    c = _cliente(banco_path, gasto_usd=TETO * 0.85, n=3)
    html = c.get("/gasto").text
    assert "80% ou mais do teto" in html
    assert "Teto atingido" not in html
    # aviso também aparece no cabeçalho de qualquer página
    assert "85% do teto mensal" in c.get("/").text


def test_gasto_teto_atingido(banco_path):
    c = _cliente(banco_path, gasto_usd=TETO, n=2)
    html = c.get("/gasto").text
    assert "Teto atingido" in html and 'role="alert"' in html
    assert 'value="100.0"' in html
    assert "Teto mensal atingido" in c.get("/").text


def test_gasto_ignora_outros_meses(banco_path):
    with Banco(banco_path) as b:
        b.registrar_chamada(modelo="m", finalidade="avaliar", chave_vaga=None, tokens_entrada=1, tokens_saida=1,
                            tokens_cache_leitura=0, tokens_cache_escrita=0, custo_usd=TETO * 5,
                            quando="2026-10-01T00:00:00+00:00")
    html = _cliente(banco_path).get("/gasto").text
    assert "Teto atingido" not in html and "80% ou mais" not in html


# --- segurança -------------------------------------------------------------------

def test_escaping_de_xss(banco_path):
    c = _cliente(banco_path)
    for caminho in ("/", "/?status=nova"):
        html = c.get(caminho).text
        assert "<script>alert" not in html
        assert "&lt;script&gt;alert" in html
        assert "<b>Mal</b>" not in html and "&lt;b&gt;Mal&lt;/b&gt;" in html
        assert 'href="javascript:' not in html
        assert "link inválido" in html
    with Banco(banco_path) as b:
        chave = next(r["chave"] for r in b.listar() if r["cargo"].startswith("X <script>"))
    html = c.get(f"/vaga/{chave}").text
    assert "<img src=x onerror" not in html and "&lt;img src=x onerror" in html
    assert 'href="javascript:' not in html
    assert "<script>alert" not in html
    # motivo escapado (Delta)
    html = c.get("/?status=bloqueada").text
    assert "data scientist <b>" not in html and "data scientist &lt;b&gt;" in html


def test_rejeita_host_estranho(banco_path):
    c = _cliente(banco_path)
    for host in ("evil.com", "127.0.0.1.evil.com", "192.168.0.5:8765"):
        r = c.get("/", headers={"Host": host})
        assert r.status_code == 400, host
        assert "Acme" not in r.text
    assert c.get("/", headers={"Host": "localhost:8765"}).status_code == 200
    assert c.get("/gasto", headers={"Host": "127.0.0.1:1234"}).status_code == 200


def test_cabecalhos_de_seguranca_e_sem_docs(banco_path):
    c = _cliente(banco_path)
    r = c.get("/")
    assert r.headers["X-Frame-Options"] == "DENY"
    assert "default-src 'none'" in r.headers["Content-Security-Policy"]
    assert c.get("/docs").status_code == 404 and c.get("/openapi.json").status_code == 404
    assert c.get("/static/painel.css").status_code == 200


def test_somente_get(banco_path):
    c = _cliente(banco_path)
    assert c.post("/", data={"x": "1"}).status_code == 405
    assert c.post("/gasto").status_code == 405


# --- CLI / servidor --------------------------------------------------------------

def test_servir_liga_em_127_0_0_1(tmp_path):
    from bot_vagas import painel

    with mock.patch("uvicorn.run") as run:
        painel.servir(porta=9999, caminho_banco=tmp_path / "x.db", caminho_custos=CUSTOS_PADRAO)
    assert run.call_args.kwargs["host"] == "127.0.0.1"
    assert run.call_args.kwargs["port"] == 9999
    with pytest.raises(ValueError):
        painel.servir(porta=1, caminho_banco=tmp_path / "x.db", caminho_custos=CUSTOS_PADRAO, host="0.0.0.0")


def test_cli_painel_usa_porta_e_loopback(tmp_path, capsys):
    from bot_vagas.cli import main

    with mock.patch("uvicorn.run") as run:
        assert main(["painel", "--porta", "8123", "--banco", str(tmp_path / "x.db")]) == 0
    assert run.call_args.kwargs["host"] == "127.0.0.1" and run.call_args.kwargs["port"] == 8123
    assert "http://127.0.0.1:8123/" in capsys.readouterr().out
