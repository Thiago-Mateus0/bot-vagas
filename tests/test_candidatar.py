"""Copiloto Gupy (semi-automático). Sem rede externa, sem API, sem Gupy real, sem o perfil real do navegador.

As páginas são FIXTURES servidas em 127.0.0.1 por um servidor local; o Chromium roda headless com um
perfil temporário. O host de teste só é aceito via `Alvos(permitir_loopback=True)`, injetado SÓ aqui.
"""

import json
import os
import stat
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from bot_vagas import cli
from bot_vagas.banco import Banco
from bot_vagas.copiloto import (
    ALVOS_PRODUCAO, Alvos, Copiloto, HostNaoPermitido, classificar, escolher_opcao, formatar_para_campo,
    preparar_pasta_perfil, validar_url_candidatura,
)
from bot_vagas.pagina import URLRecusada
from bot_vagas.respostas import valores_sensiveis

from test_preparar import RESPOSTAS_TESTE, ClienteFalhaSeChamado

FIXTURES = Path(__file__).parent / "fixtures"
ALVOS_TESTE = Alvos(permitir_loopback=True)


def _resolver_publico(host):
    return ["93.184.216.34"]


# --- infra: servidor local + Chromium headless -----------------------------------------

class _Silencioso(SimpleHTTPRequestHandler):
    def log_message(self, *a):  # sem ruído
        pass


@pytest.fixture(scope="module")
def servidor():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), partial(_Silencioso, directory=str(FIXTURES)))
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


@pytest.fixture(scope="module")
def navegador():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        yield b
        b.close()


@pytest.fixture
def pagina(navegador):
    ctx = navegador.new_context()
    pg = ctx.new_page()
    yield pg
    ctx.close()


@pytest.fixture
def pdf(tmp_path):
    p = tmp_path / "curriculo-teste.pdf"
    p.write_bytes(b"%PDF-1.4\n%fixture\n%%EOF\n")
    return p


def _copiloto(pdf, respostas=None):
    mensagens = []
    c = Copiloto(RESPOSTAS_TESTE if respostas is None else respostas, pdf, alvos=ALVOS_TESTE, log=lambda *a, **k: mensagens.append(" ".join(map(str, a))))
    c._mensagens = mensagens
    return c


def _eventos(pg):
    return pg.evaluate("() => window.__eventos"), pg.evaluate("() => window.__submits || 0")


# --- allowlist de host ------------------------------------------------------------------

@pytest.mark.parametrize("url", ["https://gupy.io/", "https://portal.gupy.io/job/1", "https://empresa.gupy.io/jobs/123",
                                 "https://a.b.gupy.io/x"])
def test_aceita_gupy_e_subdominios(url):
    assert validar_url_candidatura(url, ALVOS_PRODUCAO, resolver=_resolver_publico) == url


@pytest.mark.parametrize("url", ["https://linkedin.com/jobs/1", "https://gupy.io.evil.com/", "https://evilgupy.io/",
                                 "https://notgupy.io/", "https://exemplo.com/gupy.io", "https://127.0.0.1:8000/x",
                                 "https://localhost/x", "ftp://gupy.io/x", ""])
def test_recusa_fora_do_allowlist(url):
    with pytest.raises(URLRecusada):
        validar_url_candidatura(url, ALVOS_PRODUCAO, resolver=_resolver_publico)


def test_recusa_gupy_apontando_para_ip_privado():
    with pytest.raises(URLRecusada, match="privado"):
        validar_url_candidatura("https://x.gupy.io/", ALVOS_PRODUCAO, resolver=lambda h: ["10.0.0.1"])


def test_loopback_so_com_alvos_de_teste():
    with pytest.raises(HostNaoPermitido):
        validar_url_candidatura("http://127.0.0.1:1/x", ALVOS_PRODUCAO)
    assert validar_url_candidatura("http://127.0.0.1:1/x", ALVOS_TESTE) == "http://127.0.0.1:1/x"
    assert not ALVOS_TESTE.host_permitido("linkedin.com")


# --- reconhecimento e formatação (unitário) -----------------------------------------------

@pytest.mark.parametrize("texto,esperado", [
    ("Pretensão salarial (R$)", "pretensao_salarial.brl_mensal_minimo"),
    ("Salary expectation", "pretensao_salarial.brl_mensal_minimo"),
    ("Disponibilidade para início", "disponibilidade.inicio"),
    ("Start date", "disponibilidade.inicio"),
    ("Disponibilidade para viajar", "extras.aceita_viajar"),
    ("Carga horária", "disponibilidade.carga_horaria"),
    ("Cidade", "localizacao.cidade"), ("City", "localizacao.cidade"),
    ("Estado", "localizacao.estado"), ("País", "localizacao.pais"), ("Country", "localizacao.pais"),
    ("Gênero", "dados_demograficos.genero"), ("Gender identity", "dados_demograficos.genero"),
    ("Raça/cor", "dados_demograficos.raca_cor"), ("Pessoa com deficiência (PCD)?", "dados_demograficos.pcd"),
    ("Data de nascimento", "extras.data_nascimento"), ("Date of birth", "extras.data_nascimento"),
    ("Previsão de formatura", "extras.previsao_formatura"), ("Expected graduation", "extras.previsao_formatura"),
    ("Possui CNH?", "extras.possui_cnh"), ("Driver's license", "extras.possui_cnh"),
    ("Possui parente na empresa?", "extras.parente_na_empresa"),
    ("Já trabalhou na empresa?", "extras.ja_trabalhou_na_empresa"),
    ("Como você conheceu a vaga?", "extras.como_conheceu_a_vaga"), ("How did you hear about us?", "extras.como_conheceu_a_vaga"),
    ("Aceita trabalhar aos fins de semana?", "extras.aceita_trabalhar_fins_de_semana"),
    ("Tipo de contrato", "tipo_contrato_aceito"), ("Nível de inglês", "idiomas.ingles"),
    ("Aceita trabalho remoto?", "localizacao.aceita_remoto"), ("Disposto a se mudar?", "localizacao.aceita_mudar_de_cidade"),
    ("LinkedIn", ""), ("Telefone", ""),
])
def test_classifica_por_texto_pt_en(texto, esperado):
    assert classificar(texto) == esperado


def test_classifica_categorias_proibidas():
    assert classificar("Senha") == "login" and classificar("Password") == "login"
    assert classificar("Qualquer coisa", tipo="password") == "login"
    assert classificar("Seu e-mail", tipo="email") == "login" and classificar("E-mail") == "login"
    assert classificar("Código captcha") == "login"
    assert classificar("Li e aceito os termos", tipo="checkbox") == "consentimento"
    assert classificar("Autorizo o tratamento dos meus dados (LGPD)", tipo="checkbox") == "consentimento"
    assert classificar("I agree to the privacy policy", tipo="checkbox") == "consentimento"
    assert classificar("Por que você quer trabalhar aqui?") == "aberta"
    assert classificar("Cidade", tag="textarea") == "aberta"
    assert classificar("Describe your experience") == "aberta"
    assert classificar("Teste de lógica", ) == "aberta"
    assert classificar("Anexe seu currículo", tipo="file") == "curriculo"
    assert classificar("Resume/CV", tipo="file") == "curriculo"
    assert classificar("Portfólio", tipo="file") == ""


def test_formata_datas_e_valores():
    assert formatar_para_campo("x", "1999-01-31", "text", "dd/mm/aaaa") == "31/01/1999"
    assert formatar_para_campo("x", "1999-01-31", "date") == "1999-01-31"
    assert formatar_para_campo("x", "2029-06", "text", "mm/aaaa") == "06/2029"
    assert formatar_para_campo("x", "2029-06", "month") == "2029-06"
    assert formatar_para_campo("x", "2029-06", "date") is None  # não inventa o dia
    assert formatar_para_campo("x", "2029-06", "text", "dd/mm/aaaa") is None
    assert formatar_para_campo("x", "2029-06", "text", "Ano de formatura") == "2029"
    assert formatar_para_campo("x", 2500, "text") == "2500"
    assert formatar_para_campo("x", None, "text") is None
    assert formatar_para_campo("x", True, "text") is None  # booleano em campo de texto: ambíguo
    assert formatar_para_campo("x", ["estagio", "clt"], "text") == "estagio, clt"


def test_escolhe_opcao_por_texto_sem_ambiguidade():
    assert escolher_opcao(False, ["Sim", "Não"]) == 1
    assert escolher_opcao(True, ["Yes", "No"]) == 0
    assert escolher_opcao(True, ["Sim, aceito", "Não", "Sim, parcialmente"]) is None  # ambíguo
    assert escolher_opcao("masculino", ["Mulher cis", "Homem cis", "Não binário"]) == 1
    assert escolher_opcao("Brasil", ["Argentina", "Brazil", "Portugal"]) == 1
    assert escolher_opcao("parda", ["Branca", "Preta", "Parda", "Amarela"]) == 2
    assert escolher_opcao("avançado", ["Básico", "Intermediário", "Avançado"]) == 2
    assert escolher_opcao("2029-06", ["2028", "2029", "2030"]) is None  # formato de data: não casa
    assert escolher_opcao(2029, ["2028", "2029", "2030"]) == 1
    assert escolher_opcao(["estagio", "clt"], ["Estágio", "CLT", "PJ"]) == 0
    assert escolher_opcao("roxo", ["Sim", "Não"]) is None
    assert escolher_opcao(None, ["Sim", "Não"]) is None


# --- navegador headless contra a fixture ---------------------------------------------------

def test_preenche_campos_reconhecidos_sem_clicar_nem_enviar(pagina, servidor, pdf):
    pagina.goto(f"{servidor}/gupy_form_fixture.html")
    cp = _copiloto(pdf)
    r = cp.passo(pagina)

    v = lambda sel: pagina.locator(sel).input_value()
    assert v("#pretensao") == "2500"
    assert v("#inicio") == "imediata"
    assert v("#carga") == "conforme a vaga"
    assert v("#cidade") == "Maceió"
    assert v("#estado") == "SP"  # já vinha preenchido: NÃO sobrescreve
    assert v("#pais") == "br"
    assert v("#nasc") == "31/01/1999"
    assert v("#nasc_date") == "1999-01-31"
    assert v("#formatura") == "06/2029"
    assert v("#genero") == "m"
    assert v("#raca") == "3"
    assert v("#ingles") == "a"
    assert v("#conheceu") == "Portal"
    assert pagina.locator("input[name=pcd][value=n]").is_checked()
    assert not pagina.locator("input[name=pcd][value=s]").is_checked()
    assert pagina.locator("input[name=parente][value=n]").is_checked()
    # nulos no yaml: NÃO preenchidos e destacados
    assert not pagina.locator("input[name=cnh]:checked").count()
    assert v("#viajar") == ""
    assert pagina.locator("#viajar").get_attribute("data-bot-vagas-estado") == "pendente"
    assert pagina.locator("input[name=cnh]").first.get_attribute("data-bot-vagas-estado") == "pendente"
    # pergunta aberta e aceite: intocados e destacados
    assert v("#porque") == "" and pagina.locator("#porque").get_attribute("data-bot-vagas-estado") == "pendente"
    assert not pagina.locator("#lgpd").is_checked()
    assert pagina.locator("#lgpd").get_attribute("data-bot-vagas-estado") == "pendente"
    # não reconhecido e obrigatório: destacado
    assert v("#linkedin") == "" and pagina.locator("#linkedin").get_attribute("data-bot-vagas-estado") == "pendente"
    # upload só no input de currículo
    assert pagina.evaluate("() => document.querySelector('#cv').files.length") == 1
    assert pagina.evaluate("() => document.querySelector('#cv').files[0].name") == "curriculo-teste.pdf"
    assert pagina.evaluate("() => document.querySelector('#portfolio').files.length") == 0
    # marcas visuais e painel só leitura (pointer-events none)
    assert pagina.locator("#cidade").get_attribute("data-bot-vagas-estado") == "preenchido"
    assert "confira" in pagina.locator("#cidade").get_attribute("title")
    assert pagina.evaluate("() => getComputedStyle(document.getElementById('bot-vagas-painel')).pointerEvents") == "none"
    assert pagina.locator("#bot-vagas-painel").inner_text().startswith("bot-vagas")
    # ZERO cliques, ZERO submits, zero Enter
    eventos, submits = _eventos(pagina)
    assert eventos == [] and submits == 0
    assert pagina.url.endswith("gupy_form_fixture.html")
    assert "Cidade" in r["preenchidos"] and any("Por que" in p for p in r["pendentes"])


def test_segundo_passo_nao_repreenche_nem_sobrescreve(pagina, servidor, pdf):
    pagina.goto(f"{servidor}/gupy_form_fixture.html")
    cp = _copiloto(pdf)
    cp.passo(pagina)
    # o usuário muda um valor que o bot preencheu e limpa outro
    pagina.fill("#cidade", "Arapiraca")
    pagina.fill("#pretensao", "")
    r2 = cp.passo(pagina)
    assert r2["preenchidos"] == []
    assert pagina.locator("#cidade").input_value() == "Arapiraca"
    assert pagina.locator("#pretensao").input_value() == ""
    assert _eventos(pagina)[0] == []


def test_campo_editado_pelo_usuario_nao_e_tocado(pagina, servidor, pdf):
    pagina.goto(f"{servidor}/gupy_form_fixture.html")
    cp = _copiloto(pdf)
    cp.instalar(pagina)
    # usuário digita e apaga (campo fica vazio, mas foi editado) e escolhe um gênero diferente
    pagina.fill("#cidade", "Recife")
    pagina.fill("#cidade", "")
    pagina.select_option("#genero", "x")
    cp.passo(pagina)
    assert pagina.locator("#cidade").input_value() == ""
    assert pagina.locator("#genero").input_value() == "x"
    assert pagina.locator("#cidade").get_attribute("data-bot-vagas-estado") in (None, "")
    assert pagina.locator("#pretensao").input_value() == "2500"  # os outros seguem normalmente


def test_pagina_de_login_nao_e_tocada_e_avisa(pagina, servidor, pdf):
    pagina.goto(f"{servidor}/gupy_login_fixture.html")
    cp = _copiloto(pdf)
    r = cp.passo(pagina)
    assert r == {"preenchidos": [], "pendentes": []}
    for sel in ("#email", "#senha", "#captcha", "#cidade"):
        assert pagina.locator(sel).input_value() == ""
    assert any("PRECISA DE LOGIN" in m for m in cp._mensagens)
    assert _eventos(pagina)[0] == []
    cp.passo(pagina)
    assert sum("PRECISA DE LOGIN" in m for m in cp._mensagens) == 1  # avisa uma vez por URL


def test_aba_fora_do_allowlist_nao_e_tocada(pagina, servidor, pdf):
    pagina.goto(f"{servidor}/gupy_form_fixture.html")
    cp = Copiloto(RESPOSTAS_TESTE, pdf, alvos=ALVOS_PRODUCAO)  # produção: 127.0.0.1 não é gupy.io
    assert cp.passo(pagina) == {"preenchidos": [], "pendentes": []}
    assert pagina.locator("#cidade").input_value() == ""
    assert pagina.evaluate("() => !!window.__botVagasInstalado") is False


def test_sem_pdf_destaca_o_campo_de_curriculo(pagina, servidor, tmp_path):
    pagina.goto(f"{servidor}/gupy_form_fixture.html")
    cp = _copiloto(tmp_path / "nao-existe.pdf")
    cp.passo(pagina)
    assert pagina.evaluate("() => document.querySelector('#cv').files.length") == 0
    assert pagina.locator("#cv").get_attribute("data-bot-vagas-estado") == "pendente"
    assert any(p["motivo"].startswith("PDF") for p in cp.registro.pendentes)


def test_resumo_e_registro_sem_valores_pessoais(pagina, servidor, pdf):
    pagina.goto(f"{servidor}/gupy_form_fixture.html")
    cp = _copiloto(pdf)
    cp.passo(pagina)
    resumo = cp.resumo()
    registro = json.dumps(cp.registro.para_json(), ensure_ascii=False)
    for s in valores_sensiveis(RESPOSTAS_TESTE) + ["2500", "31/01/1999", "06/2029"]:
        assert s not in resumo, s
        assert s not in registro, s
    assert "Cidade" in resumo and "Por que" in resumo and "consentimento" in resumo
    assert "PDF anexado: sim" in resumo


# --- CLI: candidatar, marcar, painel, dedupe ------------------------------------------------

@pytest.fixture
def ambiente(tmp_path, monkeypatch):
    resp = tmp_path / "respostas.yaml"
    resp.write_text(yaml.safe_dump(RESPOSTAS_TESTE, allow_unicode=True), encoding="utf-8")
    monkeypatch.setattr("bot_vagas.pagina._resolver_real", _resolver_publico)
    perfil_nav = tmp_path / "perfil-navegador"
    return SimpleNamespace(banco=tmp_path / "t.db", saida=tmp_path / "saida", respostas=resp, raiz=tmp_path,
                           perfil_nav=perfil_nav,
                           cfg={"headless": True, "permitir_loopback": True, "max_passos": 2, "intervalo": 0.05,
                                "pasta_perfil": perfil_nav})


def _argv(amb, *extra):
    return [*extra, "--banco", str(amb.banco), "--respostas", str(amb.respostas)]


def _cli(argv, **kw):
    """Roda cli.main em uma thread própria: o sync_playwright do fixture `navegador` (mesmo módulo) não pode
    coexistir com outro sync_playwright na mesma thread. capsys continua capturando (sys.stdout é global)."""
    saida = {}

    def alvo():
        try:
            saida["codigo"] = cli.main(argv, **kw)
        except BaseException as e:  # SystemExit do argparse inclusive
            saida["erro"] = e

    t = threading.Thread(target=alvo)
    t.start()
    t.join(120)
    if "erro" in saida:
        raise saida["erro"]
    return saida["codigo"]


def test_pasta_do_perfil_fica_0700(tmp_path):
    pasta = tmp_path / "nav"
    pasta.mkdir()
    os.chmod(pasta, 0o755)
    preparar_pasta_perfil(pasta)
    assert stat.S_IMODE(pasta.stat().st_mode) == 0o700
    nova = preparar_pasta_perfil(tmp_path / "a" / "b")
    assert nova.is_dir() and stat.S_IMODE(nova.stat().st_mode) == 0o700


def test_candidatar_recusa_host_fora_do_allowlist(ambiente, capsys):
    codigo = _cli(_argv(ambiente, "candidatar", "https://www.linkedin.com/jobs/view/1"), copiloto_cfg=ambiente.cfg)
    assert codigo == 2
    err = capsys.readouterr().err
    assert "RECUSADO" in err and "gupy.io" in err
    assert not ambiente.perfil_nav.exists()  # nem chegou a abrir o navegador


def test_candidatar_prepara_sem_real_assiste_e_registra(ambiente, servidor, capsys):
    url = f"{servidor}/gupy_form_fixture.html"
    codigo = _cli(_argv(ambiente, "candidatar", url, "--empresa", "Acme", "--cargo", "Dev Backend",
                            "--saida", str(ambiente.saida)),
                      copiloto_cfg=ambiente.cfg, client_factory=ClienteFalhaSeChamado,
                      fetch=lambda u: (u, (FIXTURES / "gupy_form_fixture.html").read_text(encoding="utf-8")))
    out = capsys.readouterr().out
    assert codigo == 0, out
    assert "preparando agora SEM --real" in out
    assert "em_candidatura" in out
    assert "preencheu: Cidade" in out and "RESUMO DA ASSISTÊNCIA" in out
    assert "preencha você" in out and "bot-vagas marcar" in out
    for s in valores_sensiveis(RESPOSTAS_TESTE) + ["2500", "31/01/1999"]:
        assert s not in out, s
    assert stat.S_IMODE(ambiente.perfil_nav.stat().st_mode) == 0o700
    with Banco(ambiente.banco) as b:
        rows = b.listar("em_candidatura")
        assert len(rows) == 1
        det = json.loads(rows[0]["detalhes"])
        assert det["preparacao"]["plano"] == "neutro" and det["preparacao"]["real"] is False
        assert det["assistencia"]["pdf_anexado"] is True and det["assistencia"]["passos"] >= 1
        campos = {p["campo"] for p in det["assistencia"]["preenchidos"]}
        assert "Cidade *" in campos or "Cidade" in {c.rstrip(" *") for c in campos}
        assert any("consentimento" in p["motivo"] for p in det["assistencia"]["pendentes"])
        assert b.resumo_gasto()["chamadas"] == 0
        assert "31/01/1999" not in rows[0]["detalhes"] and "Maceió" not in json.dumps(det["assistencia"], ensure_ascii=False)


def test_candidatar_reusa_pdf_da_preparacao_e_aceita_chave(ambiente, servidor, capsys):
    url = f"{servidor}/gupy_form_fixture.html"
    arquivo = ambiente.raiz / "vaga.txt"
    arquivo.write_text("Estágio Backend\nVaga de estágio remoto com Python.", encoding="utf-8")
    assert _cli(_argv(ambiente, "preparar", "--arquivo", str(arquivo), "--empresa", "Acme", "--cargo", "Dev Backend",
                          "--url", url, "--saida", str(ambiente.saida)), client_factory=ClienteFalhaSeChamado) == 0
    with Banco(ambiente.banco) as b:
        chave = b.listar("preparada")[0]["chave"]
        pdf = json.loads(b.buscar_por_chave(chave)["detalhes"])["preparacao"]["pdf"]
    capsys.readouterr()
    codigo = _cli(_argv(ambiente, "candidatar", chave, "--saida", str(ambiente.saida)), copiloto_cfg=ambiente.cfg,
                      client_factory=ClienteFalhaSeChamado)
    out = capsys.readouterr().out
    assert codigo == 0, out
    assert "usando o PDF da preparação registrada" in out and pdf in out
    with Banco(ambiente.banco) as b:
        assert b.contar_por_status() == {"em_candidatura": 1}


def test_candidatar_pagina_de_login_funciona_e_avisa(ambiente, servidor, pdf, capsys):
    url = f"{servidor}/gupy_login_fixture.html"
    codigo = _cli(_argv(ambiente, "candidatar", url, "--pdf", str(pdf), "--empresa", "Acme", "--cargo", "X",
                            "--saida", str(ambiente.saida)), copiloto_cfg=ambiente.cfg, client_factory=ClienteFalhaSeChamado)
    out = capsys.readouterr().out
    assert codigo == 0, out
    assert "PRECISA DE LOGIN" in out
    assert "campos preenchidos pelo bot (0)" in out


def test_marcar_atualiza_status_e_dedupe_de_enviada(ambiente, servidor, pdf, capsys):
    url = f"{servidor}/gupy_form_fixture.html"
    assert _cli(_argv(ambiente, "candidatar", url, "--pdf", str(pdf), "--empresa", "Acme", "--cargo", "Dev",
                          "--saida", str(ambiente.saida)), copiloto_cfg=ambiente.cfg, client_factory=ClienteFalhaSeChamado) == 0
    capsys.readouterr()
    # marcar por URL
    assert _cli([ "marcar", url, "enviada", "--banco", str(ambiente.banco)]) == 0
    assert "em_candidatura -> enviada" in capsys.readouterr().out
    with Banco(ambiente.banco) as b:
        row = b.listar("enviada")[0]
        assert json.loads(row["detalhes"])["marcacao"]["status"] == "enviada"
        chave = row["chave"]
    # enviada: nem candidatar nem preparar de novo sem --forcar
    assert _cli(_argv(ambiente, "candidatar", chave, "--pdf", str(pdf)), copiloto_cfg=ambiente.cfg) == 2
    assert "ENVIADA" in capsys.readouterr().err
    arquivo = ambiente.raiz / "vaga.txt"
    arquivo.write_text("Estágio\nVaga.", encoding="utf-8")
    assert _cli(_argv(ambiente, "preparar", "--arquivo", str(arquivo), "--empresa", "Acme", "--cargo", "Dev",
                          "--url", url, "--saida", str(ambiente.saida)), client_factory=ClienteFalhaSeChamado) == 2
    assert "--forcar" in capsys.readouterr().err
    assert _cli(_argv(ambiente, "preparar", "--arquivo", str(arquivo), "--empresa", "Acme", "--cargo", "Dev",
                          "--url", url, "--saida", str(ambiente.saida), "--forcar"), client_factory=ClienteFalhaSeChamado) == 0
    # marcar por chave; status inválido recusado pelo argparse; vaga inexistente
    assert _cli([ "marcar", chave, "desistiu", "--banco", str(ambiente.banco)]) == 0
    with pytest.raises(SystemExit):
        _cli([ "marcar", chave, "aprovada", "--banco", str(ambiente.banco)])
    assert _cli([ "marcar", "https://x.gupy.io/nao-existe", "enviada", "--banco", str(ambiente.banco)]) == 1


def test_painel_mostra_status_novos_e_assistencia(ambiente, servidor, pdf, capsys):
    from fastapi.testclient import TestClient
    from bot_vagas.gasto import CUSTOS_PADRAO
    from bot_vagas.painel import criar_app

    url = f"{servidor}/gupy_form_fixture.html"
    assert _cli(_argv(ambiente, "candidatar", url, "--pdf", str(pdf), "--empresa", "Acme", "--cargo", "Dev",
                          "--saida", str(ambiente.saida)), copiloto_cfg=ambiente.cfg, client_factory=ClienteFalhaSeChamado) == 0
    c = TestClient(criar_app(ambiente.banco, CUSTOS_PADRAO), base_url="http://127.0.0.1")
    html = c.get("/").text
    assert "Em candidatura" in html and "status-em_candidatura" in html and "Enviadas" in html and "Desistiu" in html
    assert "Em candidatura (1)" in html
    with Banco(ambiente.banco) as b:
        chave = b.listar("em_candidatura")[0]["chave"]
    det = c.get(f"/vaga/{chave}").text
    assert "Assistência do copiloto" in det and "Copiloto assistiu o formulário" in det
    assert "Cidade" in det and "consentimento" in det
    for s in valores_sensiveis(RESPOSTAS_TESTE) + ["2500", "31/01/1999"]:
        assert s not in det, s
    assert _cli([ "marcar", chave, "enviada", "--banco", str(ambiente.banco)]) == 0
    html = c.get("/", params={"status": "enviada"}).text
    assert "Acme" in html and "Enviada" in html and "Enviadas" in html
    assert "Acme" not in c.get("/", params={"status": "em_candidatura"}).text
    assert "Você marcou: Enviada" in c.get(f"/vaga/{chave}").text


def test_login_gupy_abre_pagina_sem_tocar_em_nada(ambiente, servidor, capsys):
    """Só a mecânica (perfil 0700, aviso, espera até fechar/timeout); a URL é a fixture local, nunca a Gupy real."""
    from bot_vagas.copiloto import login_gupy

    codigo = _cli_fn(lambda: login_gupy(ambiente.perfil_nav, url=f"{servidor}/gupy_login_fixture.html", headless=True,
                                        max_segundos=0.5))
    out = capsys.readouterr().out
    assert codigo == 0
    assert "guarda a SESSÃO" in out and "0700" in out and "nunca lê" in out
    assert stat.S_IMODE(ambiente.perfil_nav.stat().st_mode) == 0o700
    assert "senha" not in out.lower().replace("senha, token", "")  # nada da página vai para o terminal


def _cli_fn(fn):
    saida = {}

    def alvo():
        try:
            saida["codigo"] = fn()
        except BaseException as e:
            saida["erro"] = e

    t = threading.Thread(target=alvo)
    t.start()
    t.join(120)
    if "erro" in saida:
        raise saida["erro"]
    return saida["codigo"]
