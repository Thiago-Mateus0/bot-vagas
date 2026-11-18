"""Modo 100% automático (piloto.py): simulação, envio, pulos, paradas, limites, perguntas abertas com o modelo.

Só fixtures em 127.0.0.1 (tests/fixtures/auto), Chromium headless, perfil temporário, cliente da API FALSO.
Nunca a Gupy real, nunca o perfil real do navegador, nenhuma chamada paga.
"""

import json
import threading
from datetime import datetime
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from bot_vagas import automacao, cli, parada, piloto
from bot_vagas.automacao import ConfigAutomacao, ConfigInvalida, carregar_config, dentro_da_janela, vagas_elegiveis
from bot_vagas.banco import Banco
from bot_vagas.copiloto import Alvos
from bot_vagas.fontes import Vaga
from bot_vagas.piloto import executar_rodada
from bot_vagas.respostas import valores_sensiveis
from bot_vagas.respostas_abertas import RespostaDescartada, responder

from test_candidatar import _cli_fn, pdf  # noqa: F401 (fixture)
from test_preparar import RESPOSTAS_TESTE, ClienteFalhaSeChamado, ClienteSequencia

AUTO = Path(__file__).parent / "fixtures" / "auto"
RAIZ = Path(__file__).resolve().parents[1]
ALVOS = Alvos(permitir_loopback=True)


class _Gravador(SimpleHTTPRequestHandler):
    pedidos: list = []

    def log_message(self, *a):
        pass

    def do_GET(self):
        type(self).pedidos.append(self.path)
        return super().do_GET()


@pytest.fixture(scope="module")
def site():
    handler = type("H", (_Gravador,), {"pedidos": []})
    srv = ThreadingHTTPServer(("127.0.0.1", 0), partial(handler, directory=str(AUTO)))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield SimpleNamespace(url=f"http://127.0.0.1:{srv.server_address[1]}", pedidos=handler.pedidos)
    srv.shutdown()


def _vaga(url, empresa="Acme", cargo="Estágio Backend"):
    return Vaga(id_fonte=url, fonte="gupy", empresa=empresa, cargo=cargo, localizacao="Remoto", remoto=True,
                descricao="Estágio remoto com Python e FastAPI.", url=url)


def _banco_com(tmp_path, *urls):
    caminho = tmp_path / "v.db"
    with Banco(caminho) as b:
        b.salvar_vagas([_vaga(u, cargo=f"Estágio {i}") for i, u in enumerate(urls)])
        for r in b.listar():
            b.atualizar_status(r["chave"], "nova", detalhes=json.dumps({"filtro": "passou"}))
    return caminho


def _rodar(tmp_path, pdf, banco_path, cfg, *, responder_para=lambda row: None, parar=lambda: False,
           restantes=lambda site: 99, notificar=None, credenciais=None):
    eventos = []
    saida = {}

    def alvo():
        with Banco(banco_path) as b:
            vagas = b.listar()
            saida["r"] = executar_rodada(
                vagas, banco=b, cfg=cfg, preparar_pdf=lambda row: pdf, respostas=RESPOSTAS_TESTE,
                responder_para=responder_para, pasta_perfil=tmp_path / "nav", capturas=tmp_path / "capturas",
                restantes=restantes, alvos=ALVOS, headless=True, parar=parar,
                notificar=notificar or eventos.append, log=lambda *a, **k: None, credenciais=credenciais)

    _cli_fn(lambda: alvo() or 0)
    with Banco(banco_path) as b:
        linhas = {r["url"].rsplit("/", 1)[-1]: dict(r) for r in b.listar()}
    return saida["r"], linhas, eventos


def _auto(linha):
    return json.loads(linha["detalhes"])["automatico"]


SIM = ConfigAutomacao(modo="simulacao")
ENVIO = ConfigAutomacao(modo="envio")


# --- simulação e envio ---------------------------------------------------------------------------------

def test_simulacao_vai_ate_o_fim_e_nao_envia(tmp_path, pdf, site):
    site.pedidos.clear()
    banco = _banco_com(tmp_path, f"{site.url}/vaga_curta.html")
    r, linhas, _ = _rodar(tmp_path, pdf, banco, SIM)
    l = linhas["vaga_curta.html"]
    assert l["status"] == "simulada", l["motivo"]
    assert "Finalizar candidatura" in l["motivo"]
    assert not any(p.startswith("/sucesso.html") for p in site.pedidos)  # NUNCA clicou no botão final
    a = _auto(l)
    assert a["capturas"] and Path(a["capturas"][-1]).exists() and a["capturas"][-1].endswith(".png")
    assert a["tentativas"][-1]["modo"] == "simulacao" and r.por_resultado == {"simulada": 1}
    nomes = {p["campo"] for p in a["campos"]["preenchidos"]}
    assert any("Cidade" in n for n in nomes) and any("termos" in n.lower() for n in nomes)
    assert a["campos"]["pdf_anexado"] is True


def test_envio_de_verdade_marca_enviada_com_confirmacao(tmp_path, pdf, site):
    site.pedidos.clear()
    banco = _banco_com(tmp_path, f"{site.url}/vaga_curta.html")
    r, linhas, _ = _rodar(tmp_path, pdf, banco, ENVIO)
    l = linhas["vaga_curta.html"]
    assert l["status"] == "enviada", l["motivo"]
    assert any(p.startswith("/sucesso.html") for p in site.pedidos)
    a = _auto(l)
    assert len(a["capturas"]) == 2  # formulário antes de enviar + confirmação (§15.6)
    assert json.loads(l["detalhes"])["marcacao"]["por"] == "bot (modo automático)"


def test_pergunta_aberta_com_modelo_aceita_termos_e_ignora_marketing(tmp_path, pdf, site, perfil):
    site.pedidos.clear()
    banco = _banco_com(tmp_path, f"{site.url}/vaga.html")
    texto = "Quero aplicar Python e FastAPI em backend e aprender com o time."
    cliente = ClienteSequencia([json.dumps({"consegue_responder": True, "resposta": texto})])
    vaga = _vaga(f"{site.url}/vaga.html")

    def responder_para(row):
        return lambda pergunta: responder(pergunta, vaga, perfil, "pt", cliente)[0]

    r, linhas, _ = _rodar(tmp_path, pdf, banco, ENVIO, responder_para=responder_para)
    l = linhas["vaga.html"]
    assert l["status"] == "enviada", (l["motivo"], json.dumps(_auto(l), ensure_ascii=False)[:3000], site.pedidos)
    assert "/sucesso.html?mkt=0" in site.pedidos  # LGPD marcada, marketing NÃO
    assert len(cliente.chamadas) == 1
    prompt = json.dumps(cliente.chamadas[0], ensure_ascii=False)
    from bot_vagas.avaliador import _perfil_para_prompt

    no_perfil = _perfil_para_prompt(perfil)  # o perfil (sem contato) vai ao modelo por desenho
    for s in valores_sensiveis(RESPOSTAS_TESTE):
        if s not in no_perfil:
            assert s not in prompt, s  # nada que só existe em respostas.yaml vai ao modelo
    for s in ("1850", "1999-01-31", "feminino", "amarela", "2031-12"):
        assert s not in prompt, s
    assert perfil.contato.email not in prompt and perfil.contato.telefone not in prompt
    assert "Por que você quer trabalhar conosco?" in prompt


def test_pergunta_aberta_sem_modelo_pula_a_vaga(tmp_path, pdf, site):
    site.pedidos.clear()
    banco = _banco_com(tmp_path, f"{site.url}/vaga.html")
    _, linhas, _ = _rodar(tmp_path, pdf, banco, ENVIO)
    l = linhas["vaga.html"]
    assert l["status"] == "pulada" and "Por que" in l["motivo"]
    assert not any(p.startswith("/sucesso.html") for p in site.pedidos)


# --- pulos (a rodada continua) --------------------------------------------------------------------

@pytest.mark.parametrize("pagina,trecho", [
    ("cpf.html", "CPF"), ("travado.html", "travado"), ("externo.html", "fora da Gupy"),
    ("ja_candidatado.html", "já se candidatou"), ("sem_botao.html", "não achei botão"),
])
def test_pula_e_registra_motivo(tmp_path, pdf, site, pagina, trecho):
    banco = _banco_com(tmp_path, f"{site.url}/{pagina}", f"{site.url}/vaga_curta.html")
    r, linhas, eventos = _rodar(tmp_path, pdf, banco, SIM)
    assert linhas[pagina]["status"] == "pulada" and trecho in linhas[pagina]["motivo"], linhas[pagina]["motivo"]
    assert linhas["vaga_curta.html"]["status"] == "simulada"  # a rodada seguiu para a próxima
    assert eventos == []


# --- paradas (a rodada para e avisa) ----------------------------------------------------------------

@pytest.mark.parametrize("pagina,evento,trecho", [("captcha.html", "login", "captcha"),
                                                   ("login.html", "login", "login")])
def test_captcha_e_login_param_a_rodada_e_avisam(tmp_path, pdf, site, pagina, evento, trecho):
    banco = _banco_com(tmp_path, f"{site.url}/{pagina}", f"{site.url}/vaga_curta.html")
    r, linhas, eventos = _rodar(tmp_path, pdf, banco, ENVIO)
    assert eventos == [evento] and trecho in r.motivo_fim
    assert linhas[pagina]["status"] == "nova"  # nada aconteceu: volta para tentar depois
    assert linhas["vaga_curta.html"]["status"] == "nova"  # não seguiu para a próxima


def test_parar_interrompe_a_rodada(tmp_path, pdf, site):
    banco = _banco_com(tmp_path, f"{site.url}/vaga_curta.html", f"{site.url}/travado.html")
    r, linhas, _ = _rodar(tmp_path, pdf, banco, SIM, parar=lambda: True)
    assert r.motivo_fim == "parada pedida (arquivo PARAR)" and r.tentativas == 0
    assert {l["status"] for l in linhas.values()} == {"nova"}


def test_limite_diario_para_a_rodada(tmp_path, pdf, site):
    banco = _banco_com(tmp_path, f"{site.url}/vaga_curta.html", f"{site.url}/sem_botao.html")
    chamadas = []

    def restantes(site):
        chamadas.append(site)
        return 1 if len(chamadas) == 1 else 0  # cabe só uma hoje

    r, linhas, _ = _rodar(tmp_path, pdf, banco, SIM, restantes=restantes)
    assert r.tentativas == 1 and r.motivo_fim == "limite diário"
    assert sorted(l["status"] for l in linhas.values()) == ["nova", "simulada"]


# --- automacao.py --------------------------------------------------------------------------------

def test_config_padrao_e_segura(tmp_path):
    cfg = carregar_config(tmp_path / "nao-existe.yaml")
    assert cfg.ativo is False and cfg.modo == "simulacao" and cfg.usar_modelo_para_perguntas_abertas is False
    exemplo = yaml.safe_load((RAIZ / "automacao.example.yaml").read_text(encoding="utf-8"))
    assert exemplo["ativo"] is False and exemplo["modo"] == "simulacao"
    assert exemplo["usar_modelo_para_perguntas_abertas"] is False
    assert carregar_config(RAIZ / "automacao.example.yaml") == ConfigAutomacao()


@pytest.mark.parametrize("ruim", [{"modo": "turbo"}, {"chave_errada": 1}, {"janela_inicio": "25:00"},
                                  {"pausa_min_s": 10, "pausa_max_s": 5}, {"limite_diario": -1}])
def test_config_invalida(tmp_path, ruim):
    arq = tmp_path / "a.yaml"
    arq.write_text(yaml.safe_dump(ruim), encoding="utf-8")
    with pytest.raises(ConfigInvalida):
        carregar_config(arq)


def test_janela_de_horario():
    cfg = ConfigAutomacao(janela_inicio="14:00", janela_fim="17:00")
    assert dentro_da_janela(cfg, datetime(2026, 11, 16, 14, 30))
    assert not dentro_da_janela(cfg, datetime(2026, 11, 16, 9, 0))
    assert not dentro_da_janela(cfg, datetime(2026, 11, 16, 17, 1))


def test_vagas_elegiveis_so_gupy_filtradas_e_sem_repetir(tmp_path):
    urls = {"nova_ok": "https://acme.gupy.io/jobs/1", "bloqueada": "https://acme.gupy.io/jobs/2",
            "enviada": "https://acme.gupy.io/jobs/3", "greenhouse": "https://boards.greenhouse.io/x/jobs/4",
            "simulada": "https://acme.gupy.io/jobs/5", "pulada": "https://acme.gupy.io/jobs/6",
            "sem_filtro": "https://acme.gupy.io/jobs/7", "preparada_mao": "https://acme.gupy.io/jobs/8"}
    with Banco(tmp_path / "v.db") as b:
        b.salvar_vagas([_vaga(u, cargo=k) for k, u in urls.items()])
        for r in b.listar():
            k = r["cargo"]
            status = {"bloqueada": "bloqueada", "enviada": "enviada", "simulada": "simulada", "pulada": "pulada",
                      "preparada_mao": "preparada"}.get(k, "nova")
            det = {} if k in ("sem_filtro", "preparada_mao") else {"filtro": "passou"}
            b.atualizar_status(r["chave"], status, detalhes=json.dumps(det))
        assert {r["cargo"] for r in vagas_elegiveis(b, SIM)} == {"nova_ok"}
        assert {r["cargo"] for r in vagas_elegiveis(b, ENVIO)} == {"nova_ok", "simulada"}
        assert vagas_elegiveis(b, ConfigAutomacao(exigir_nota_do_modelo=True)) == []


def test_tentativas_hoje_conta_por_site(tmp_path):
    with Banco(tmp_path / "v.db") as b:
        b.salvar_vagas([_vaga("https://a.gupy.io/1"), _vaga("https://a.gupy.io/2", cargo="y")])
        hoje = datetime.now().date().isoformat()
        for i, r in enumerate(b.listar()):
            b.atualizar_status(r["chave"], "simulada", detalhes=json.dumps({"automatico": {"tentativas": [
                {"dia": hoje, "site": "gupy"}, {"dia": "2000-01-01", "site": "gupy"}]}}))
        assert automacao.tentativas_hoje(b) == {"gupy": 2}


# --- respostas_abertas.py ---------------------------------------------------------------------------

def test_resposta_que_inventa_e_descartada(perfil):
    vaga = _vaga("https://a.gupy.io/1", empresa="Acme")
    inventada = json.dumps({"consegue_responder": True,
                            "resposta": "Trabalhei 5 anos com Kubernetes e Scala na Acme."})
    with pytest.raises(RespostaDescartada) as e:
        responder("Por que você?", vaga, perfil, "pt", ClienteSequencia([inventada]))
    assert e.value.resposta is not None  # a chamada aconteceu: o gasto ainda é registrado
    nao_sabe = json.dumps({"consegue_responder": False, "resposta": ""})
    with pytest.raises(RespostaDescartada, match="não está no perfil"):
        responder("Qual seu CPF?", vaga, perfil, "pt", ClienteSequencia([nao_sabe]))
    ok = json.dumps({"consegue_responder": True, "resposta": "Quero crescer em backend com Python na Acme."})
    assert "Acme" in responder("Por que a Acme?", vaga, perfil, "pt", ClienteSequencia([ok]))[0]


# --- CLI ------------------------------------------------------------------------------------------

@pytest.fixture
def ambiente_cli(tmp_path, monkeypatch, site):
    resp = tmp_path / "respostas.yaml"
    resp.write_text(yaml.safe_dump(RESPOSTAS_TESTE, allow_unicode=True), encoding="utf-8")
    auto = tmp_path / "automacao.yaml"
    monkeypatch.setattr(automacao, "site_da_url", lambda url: "gupy" if "127.0.0.1" in url else None)
    banco = _banco_com(tmp_path, f"{site.url}/vaga_curta.html")
    cfg = {"headless": True, "permitir_loopback": True, "pasta_perfil": tmp_path / "nav",
           "capturas": tmp_path / "capturas", "pasta_logs": tmp_path / "logs", "pausa": 0}
    argv = ["--banco", str(banco), "--respostas", str(resp), "--automacao", str(auto), "--saida", str(tmp_path / "s")]
    return SimpleNamespace(banco=banco, auto=auto, cfg=cfg, argv=argv, tmp=tmp_path)


def test_cli_automatico_simulacao_fim_a_fim(ambiente_cli, capsys, site):
    site.pedidos.clear()
    ambiente_cli.auto.write_text("modo: simulacao\n", encoding="utf-8")
    codigo = _cli_fn(lambda: cli.main(["automatico", "--agora", *ambiente_cli.argv], copiloto_cfg=ambiente_cli.cfg,
                                      client_factory=ClienteFalhaSeChamado))
    out = capsys.readouterr().out
    assert codigo == 0, out
    assert "SIMULAÇÃO" in out and "simulada: 1" in out
    assert not any(p.startswith("/sucesso.html") for p in site.pedidos)
    with Banco(ambiente_cli.banco) as b:
        assert b.contar_por_status() == {"simulada": 1} and b.resumo_gasto()["chamadas"] == 0
    logs = list((ambiente_cli.tmp / "logs").glob("automatico-*.jsonl"))
    assert len(logs) == 1
    bruto = logs[0].read_text(encoding="utf-8").lower()
    for s in valores_sensiveis(RESPOSTAS_TESTE):
        assert s.lower() not in bruto, s


def test_cli_automatico_respeita_janela_e_simular(ambiente_cli, capsys, site, monkeypatch):
    ambiente_cli.auto.write_text("modo: envio\njanela_inicio: '00:00'\njanela_fim: '00:00'\n", encoding="utf-8")
    assert cli.main(["automatico", *ambiente_cli.argv], copiloto_cfg=ambiente_cli.cfg) == 0
    assert "fora da janela" in capsys.readouterr().out
    site.pedidos.clear()
    codigo = _cli_fn(lambda: cli.main(["automatico", "--agora", "--simular", *ambiente_cli.argv],
                                      copiloto_cfg=ambiente_cli.cfg))
    assert codigo == 0 and "SIMULAÇÃO" in capsys.readouterr().out
    assert not any(p.startswith("/sucesso.html") for p in site.pedidos)  # --simular vence modo: envio


def test_cli_modelo_so_com_autorizacao_no_yaml(ambiente_cli, capsys):
    ambiente_cli.auto.write_text("modo: simulacao\n", encoding="utf-8")

    def nao_criar():
        raise AssertionError("sem usar_modelo_para_perguntas_abertas: true o cliente não pode ser criado")

    assert _cli_fn(lambda: cli.main(["automatico", "--agora", *ambiente_cli.argv], copiloto_cfg=ambiente_cli.cfg,
                                    client_factory=nao_criar)) == 0


def test_rodar_com_automacao_desligada_nao_candidata(ambiente_cli, capsys):
    arq = ambiente_cli.tmp / "fontes.yaml"
    arq.write_text("{}\n", encoding="utf-8")
    codigo = cli.main(["rodar", "--fontes", str(arq), *ambiente_cli.argv], copiloto_cfg=ambiente_cli.cfg)
    assert codigo == 1  # fontes.yaml vazio é erro na busca
    assert "modo automático desligado" in capsys.readouterr().out
    with Banco(ambiente_cli.banco) as b:
        assert b.contar_por_status() == {"nova": 1}


def test_cli_automatico_com_parar_nao_comeca(ambiente_cli, capsys):
    parada.pedir_parada("teste")
    assert cli.main(["automatico", "--agora", *ambiente_cli.argv], copiloto_cfg=ambiente_cli.cfg) == 4


def test_banner_de_cookies_e_recusado_e_nao_bloqueia_o_clique(tmp_path, pdf, site):
    site.pedidos.clear()
    banco = _banco_com(tmp_path, f"{site.url}/cookies.html")
    _, linhas, _ = _rodar(tmp_path, pdf, banco, SIM)
    assert linhas["cookies.html"]["status"] == "simulada", linhas["cookies.html"]["motivo"]
    assert not any(p.startswith("/aceitou.html") for p in site.pedidos)  # recusou, nunca aceitou


def test_parada_por_login_registra_onde_parou(tmp_path, pdf, site):
    banco = _banco_com(tmp_path, f"{site.url}/login.html")
    r, linhas, _ = _rodar(tmp_path, pdf, banco, SIM)
    assert "127.0.0.1" in r.motivo_fim and "login-gupy <link da vaga>" in r.motivo_fim


def test_login_gupy_aceita_link_de_vaga_so_da_gupy(tmp_path, capsys):
    codigo = cli.main(["login-gupy", "https://www.linkedin.com/jobs/1"],
                      copiloto_cfg={"pasta_perfil": tmp_path / "nav", "headless": True})
    assert codigo == 2 and "RECUSADO" in capsys.readouterr().err
    assert not (tmp_path / "nav").exists()


def test_login_gupy_abre_o_link_da_vaga(tmp_path, capsys, site):
    site.pedidos.clear()
    cfg = {"pasta_perfil": tmp_path / "nav", "headless": True, "permitir_loopback": True, "max_segundos": 1}
    codigo = _cli_fn(lambda: cli.main(["login-gupy", f"{site.url}/vaga.html"], copiloto_cfg=cfg))
    assert codigo == 0 and "Candidatar-se" in capsys.readouterr().out
    assert "/vaga.html" in site.pedidos


# --- páginas que carregam devagar (a Gupy é uma SPA) ---------------------------------------------------

def test_tela_de_login_da_gupy_que_chega_em_branco_e_so_pede_email_para_a_rodada(tmp_path, pdf, site):
    """Caso real: depois do 'Candidatar-se', /candidates/signin chegou em branco e o bot disse 'não achei
    botão'. Tem que esperar, reconhecer a tela de login pelo endereço e NUNCA clicar em 'Continuar'."""
    site.pedidos.clear()
    banco = _banco_com(tmp_path, f"{site.url}/vaga_signin.html", f"{site.url}/vaga_curta.html")
    r, linhas, eventos = _rodar(tmp_path, pdf, banco, SIM)
    assert eventos == ["login"] and "pediu login" in r.motivo_fim, r.motivo_fim
    assert linhas["vaga_signin.html"]["status"] == "nova"  # volta para tentar depois do login
    assert not any(p.startswith("/passou_do_login.html") for p in site.pedidos)


def test_login_que_redireciona_sozinho_segue_para_o_formulario(tmp_path, pdf, site):
    banco = _banco_com(tmp_path, f"{site.url}/vaga_sso.html")
    _, linhas, eventos = _rodar(tmp_path, pdf, banco, SIM)
    assert linhas["vaga_sso.html"]["status"] == "simulada", linhas["vaga_sso.html"]["motivo"]
    assert eventos == []


def test_pagina_que_nao_carrega_nao_vira_pulada(tmp_path, pdf, site, monkeypatch):
    monkeypatch.setattr(piloto, "ESPERA_RENDER_MS", 1500)
    banco = _banco_com(tmp_path, f"{site.url}/em_branco.html", f"{site.url}/vaga_curta.html")
    _, linhas, eventos = _rodar(tmp_path, pdf, banco, SIM)
    assert linhas["em_branco.html"]["status"] == "nova" and "não carregou" in linhas["em_branco.html"]["motivo"]
    assert linhas["vaga_curta.html"]["status"] == "simulada" and eventos == []


@pytest.mark.parametrize("url,esperado", [
    ("https://empresa.gupy.io/candidates/signin?x=1", True), ("https://empresa.gupy.io/login", True),
    ("https://login.gupy.io/qualquer", True), ("https://empresa.gupy.io/job/abc", False),
    ("https://empresa.gupy.io/candidates/jobs/1/apply", False),
])
def test_url_de_login(url, esperado):
    assert piloto._eh_url_de_login(url) is esperado


# --- "Entrar com LinkedIn" (OAuth fora da Gupy) -----------------------------------------------------
# Caso real: o 'Candidatar-se' da Starian levou a www.linkedin.com/uas/login e o bot marcou "fora da Gupy".
# O linkedin.com é INTERCEPTADO pelo Playwright (route): nenhuma requisição sai da máquina.

def _oauth(tmp_path, pdf, site, html_linkedin):
    from playwright.sync_api import sync_playwright

    from bot_vagas.piloto import CamposAutomaticos, candidatar_uma

    externos = []

    def rodar():
        with sync_playwright() as p:
            nav = p.chromium.launch(headless=True)
            try:
                ctx = nav.new_context()

                def falso(route):
                    externos.append(route.request.url)
                    route.fulfill(status=200, content_type="text/html", body=html_linkedin)

                ctx.route("https://www.linkedin.com/**", falso)
                campos = CamposAutomaticos(RESPOSTAS_TESTE, pdf, alvos=ALVOS, log=lambda *a, **k: None)
                return candidatar_uma(ctx, f"{site.url}/vaga_oauth.html", campos, enviar=False,
                                      capturas=tmp_path / "capturas", nome="t", log=lambda *a, **k: None)
            finally:
                nav.close()

    return _cli_fn(rodar), externos


def test_login_do_linkedin_para_a_rodada_em_vez_de_pular(tmp_path, pdf, site, monkeypatch):
    monkeypatch.setattr(piloto, "ESPERA_SSO_MS", 1000)
    monkeypatch.setattr(piloto, "ESPERA_TRANSICAO_MS", 2000)  # página sem formulário: espera o tempo longo
    r, externos = _oauth(tmp_path, pdf, site, "<html><body><h1>Entrar no LinkedIn</h1></body></html>")
    assert r.resultado == "parar_rodada" and r.evento == "login", r.motivo
    assert "LinkedIn" in r.motivo and "login-gupy" in r.motivo
    assert r.sso == ("caiu", "LinkedIn")
    assert externos and all(u.startswith("https://www.linkedin.com/") for u in externos)


def test_linkedin_ja_logado_devolve_para_a_gupy_e_segue(tmp_path, pdf, site, monkeypatch):
    volta = ("<html><body><script>const o = new URLSearchParams(location.search).get('volta');"
             "setTimeout(() => location.replace(o + '/passo1.html?prox=passo2_curto.html'), 300);</script></body></html>")
    r, _ = _oauth(tmp_path, pdf, site, volta)
    assert r.resultado == "simulada", r.motivo


@pytest.mark.parametrize("url,esperado", [
    ("https://www.linkedin.com/uas/login?x=1", "LinkedIn"), ("https://www.linkedin.com/oauth/v2/authorization", "LinkedIn"),
    ("https://accounts.google.com/o/oauth2/auth", "Google"), ("https://www.linkedin.com/jobs/view/1", None),
    ("https://empresa.com.br/vagas", None),
])
def test_provedor_de_login_externo(url, esperado):
    assert piloto.provedor_de_login_externo(url) == esperado


def test_marcar_fila_devolve_vaga_pulada_ao_modo_automatico(tmp_path, capsys):
    banco = _banco_com(tmp_path, "https://acme.gupy.io/job/1")
    with Banco(banco) as b:
        chave = b.listar()[0]["chave"]
        b.atualizar_status(chave, "pulada", detalhes=json.dumps({"filtro": "passou"}))
    assert cli.main(["marcar", "--banco", str(banco), chave, "fila"]) == 0
    with Banco(banco) as b:
        assert b.listar()[0]["status"] == "nova"
        assert len(vagas_elegiveis(b, SIM)) == 1
        b.atualizar_status(chave, "enviada")
    assert cli.main(["marcar", "--banco", str(banco), chave, "fila"]) == 2  # enviada não volta


def test_tela_de_transicao_validando_informacoes_e_esperada(tmp_path, pdf, site):
    """Caso real (Starian): depois do LinkedIn, /candidates/auth/linkedin mostra 'Validando suas informações
    (Isso pode levar alguns segundos)' e o bot desistia com 'não achei botão'."""
    banco = _banco_com(tmp_path, f"{site.url}/vaga_auth.html")
    _, linhas, eventos = _rodar(tmp_path, pdf, banco, SIM)
    assert linhas["vaga_auth.html"]["status"] == "simulada", linhas["vaga_auth.html"]["motivo"]
    assert eventos == []
    from bot_vagas import sessao_sso
    assert [(d["evento"], d["provedor"]) for d in sessao_sso.ler()] == [("passou", "LinkedIn")]


def test_transicao_que_nunca_termina_volta_para_a_fila(tmp_path, pdf, site, monkeypatch):
    monkeypatch.setattr(piloto, "ESPERA_TRANSICAO_MS", 1500)
    monkeypatch.setattr(piloto, "ESPERA_REOLHAR_MS", 500)
    banco = _banco_com(tmp_path, f"{site.url}/travada_validando.html")
    _, linhas, _ = _rodar(tmp_path, pdf, banco, SIM)
    assert linhas["travada_validando.html"]["status"] == "nova"
    assert "não carregou" in linhas["travada_validando.html"]["motivo"]


def test_screenshots_ficam_so_para_o_dono(tmp_path, pdf, site):
    banco = _banco_com(tmp_path, f"{site.url}/vaga_curta.html")
    _rodar(tmp_path, pdf, banco, SIM)
    fotos = list((tmp_path / "capturas").glob("*.png"))
    assert fotos and all((f.stat().st_mode & 0o777) == 0o600 for f in fotos)


def test_historico_da_sessao_sso_conta_quedas_e_e_privado():
    from datetime import datetime, timedelta, timezone

    from bot_vagas import sessao_sso

    agora = datetime(2026, 11, 20, 15, 0, tzinfo=timezone.utc)
    sessao_sso.registrar("passou", "LinkedIn", agora - timedelta(days=9))
    sessao_sso.registrar("caiu", "LinkedIn", agora - timedelta(days=8))  # fora da janela de 7 dias
    sessao_sso.registrar("passou", "LinkedIn", agora - timedelta(days=2))
    sessao_sso.registrar("caiu", "LinkedIn", agora - timedelta(days=1))
    sessao_sso.registrar("qualquer", "LinkedIn")  # ignorado
    (linha,) = sessao_sso.resumo(agora)
    assert "LinkedIn" in linha and "quedas nos últimos 7 dias: 1" in linha
    assert (sessao_sso.caminho().stat().st_mode & 0o777) == 0o600
    texto = sessao_sso.caminho().read_text()
    assert "@" not in texto and "http" not in texto  # só data, provedor e evento


@pytest.mark.parametrize("pagina", ["popup_fica.html", "popup_fecha.html"])
def test_aba_em_branco_aberta_pela_pagina_e_ignorada(tmp_path, pdf, site, pagina):
    """Caso real (Starian e v(dev)): no /apply a Gupy abriu uma aba about:blank; o bot seguiu nela e disse
    'fora da Gupy (?)' ou caiu com TargetClosedError quando ela fechou."""
    banco = _banco_com(tmp_path, f"{site.url}/{pagina}")
    _, linhas, eventos = _rodar(tmp_path, pdf, banco, SIM)
    assert linhas[pagina]["status"] == "simulada", linhas[pagina]["motivo"]
    assert eventos == []


def test_aba_fechada_e_reconhecida():
    class TargetClosedError(Exception):
        pass

    assert piloto._aba_fechada(TargetClosedError("x"))
    assert piloto._aba_fechada(RuntimeError("Execution context was destroyed, most likely because of a navigation"))
    assert not piloto._aba_fechada(ValueError("outra coisa"))


def test_spinner_do_formulario_e_esperado(tmp_path, pdf, site):
    """Caso real (Starian, /apply): cabeçalho e rodapé prontos e o formulário ainda no spinner; o bot achou que
    a página tinha carregado e disse 'não achei botão'."""
    banco = _banco_com(tmp_path, f"{site.url}/spinner.html")
    _, linhas, _ = _rodar(tmp_path, pdf, banco, SIM)
    assert linhas["spinner.html"]["status"] == "simulada", linhas["spinner.html"]["motivo"]


def test_barra_de_etapas_nao_e_spinner(tmp_path, pdf, site, monkeypatch):
    monkeypatch.setattr(piloto, "ESPERA_TRANSICAO_MS", 60000)  # se confundisse, estouraria o tempo do teste
    banco = _banco_com(tmp_path, f"{site.url}/etapas.html")
    _, linhas, _ = _rodar(tmp_path, pdf, banco, SIM)
    assert linhas["etapas.html"]["status"] == "simulada", linhas["etapas.html"]["motivo"]


# --- login da Gupy com o botão "LinkedIn" (opcional) ---------------------------------------------------

def test_login_da_gupy_pelo_botao_linkedin_quando_ligado(tmp_path, pdf, site):
    """Caso real (Starian): a Gupy pediu login com e-mail/senha ou Google/LinkedIn, e a sessão do LinkedIn
    estava válida. Com entrar_com_linkedin, o bot clica só no botão 'LinkedIn' e segue; nada é digitado."""
    site.pedidos.clear()
    cfg = ConfigAutomacao(modo="simulacao", entrar_com_linkedin=True)
    banco = _banco_com(tmp_path, f"{site.url}/vaga_social.html")
    _, linhas, eventos = _rodar(tmp_path, pdf, banco, cfg)
    assert linhas["vaga_social.html"]["status"] == "simulada", linhas["vaga_social.html"]["motivo"]
    assert eventos == []
    assert not any(p.startswith(("/google.html", "/senha_sem_senha.html")) for p in site.pedidos)


def test_login_da_gupy_sem_a_opcao_para_a_rodada_sem_clicar(tmp_path, pdf, site):
    site.pedidos.clear()
    banco = _banco_com(tmp_path, f"{site.url}/vaga_social.html")
    r, linhas, eventos = _rodar(tmp_path, pdf, banco, SIM)
    assert eventos == ["login"] and linhas["vaga_social.html"]["status"] == "nova"
    assert not any("/candidates/auth/" in p for p in site.pedidos)


def test_aba_original_fechada_ao_abrir_a_vaga(tmp_path, pdf, site):
    """Caso real (v(dev)): TargetClosedError logo ao abrir; a Gupy seguiu numa aba nova."""
    banco = _banco_com(tmp_path, f"{site.url}/fecha_e_abre.html")
    _, linhas, _ = _rodar(tmp_path, pdf, banco, SIM)
    assert linhas["fecha_e_abre.html"]["status"] == "simulada", linhas["fecha_e_abre.html"]["motivo"]


def test_fechar_a_janela_do_bot_para_a_rodada_sem_culpar_a_vaga(tmp_path, pdf, site):
    """Fechar a janela do Chromium durante a rodada (ex.: na pausa) = 'janela fechada', e as vagas ficam na fila
    (antes virava 'erro do navegador (TargetClosedError)' na vaga seguinte)."""
    banco = _banco_com(tmp_path, f"{site.url}/vaga_curta.html", f"{site.url}/sem_botao.html")
    estado = {"n": 0}

    def pausa():
        return 1.0

    import bot_vagas.piloto as pl
    original = pl.candidatar_uma

    def fecha_depois_da_primeira(ctx, *a, **k):
        r = original(ctx, *a, **k)
        estado["n"] += 1
        if estado["n"] == 1:
            for pg in list(ctx.pages):
                pg.close()  # como o usuário fechando a última janela
            ctx.close()
        return r

    saida = {}

    def alvo():
        with Banco(banco) as b:
            saida["r"] = executar_rodada(
                b.listar(), banco=b, cfg=SIM, preparar_pdf=lambda row: pdf, respostas=RESPOSTAS_TESTE,
                responder_para=lambda row: None, pasta_perfil=tmp_path / "nav", capturas=tmp_path / "capturas",
                restantes=lambda s: 99, alvos=ALVOS, headless=True, pausa=pausa, log=lambda *a, **k: None)

    import pytest as _pt
    mp = _pt.MonkeyPatch()
    mp.setattr(pl, "candidatar_uma", fecha_depois_da_primeira)
    try:
        _cli_fn(lambda: alvo() or 0)
    finally:
        mp.undo()
    with Banco(banco) as b:
        linhas = {r["url"].rsplit("/", 1)[-1]: dict(r) for r in b.listar()}
    assert saida["r"].motivo_fim == "janela fechada", saida["r"].motivo_fim
    assert linhas["vaga_curta.html"]["status"] == "simulada"
    assert linhas["sem_botao.html"]["status"] == "nova"  # não foi culpada


def test_rodada_normal_nao_termina_como_janela_fechada(tmp_path, pdf, site):
    banco = _banco_com(tmp_path, f"{site.url}/vaga_curta.html")
    r, _, _ = _rodar(tmp_path, pdf, banco, SIM)
    assert r.motivo_fim == ""


def test_linkedin_em_branco_demorando_mais_que_6s_ainda_volta(tmp_path, pdf, site):
    """Caso real (Starian, 17/11): linkedin.com/uas/login ficou EM BRANCO por mais de 6 s com a sessão válida e o
    bot disse 'fora da Gupy (www.linkedin.com)'. Em branco = ainda redirecionando: tem que esperar."""
    volta = ("<html><body><script>const o = new URLSearchParams(location.search).get('volta');"
             "setTimeout(() => location.replace(o + '/passo1.html?prox=passo2_curto.html'), 9000);</script></body></html>")
    r, _ = _oauth(tmp_path, pdf, site, volta)
    assert r.resultado == "simulada", r.motivo


def test_linkedin_com_formulario_de_login_para_logo(tmp_path, pdf, site, monkeypatch):
    monkeypatch.setattr(piloto, "ESPERA_SSO_MS", 1000)
    form = "<html><body><h1>Olá novamente</h1><input name='session_password' type='password'></body></html>"
    import time as _t
    t0 = _t.monotonic()
    r, _ = _oauth(tmp_path, pdf, site, form)
    assert r.resultado == "parar_rodada" and r.sso == ("caiu", "LinkedIn")
    assert _t.monotonic() - t0 < 20  # não ficou os 30 s esperando


def test_gupy_recusa_o_login_pelo_linkedin_para_com_motivo_claro(tmp_path, pdf, site):
    cfg = ConfigAutomacao(modo="simulacao", entrar_com_linkedin=True)
    banco = _banco_com(tmp_path, f"{site.url}/vaga_recusa.html")
    r, linhas, eventos = _rodar(tmp_path, pdf, banco, cfg)
    assert eventos == ["login"] and "não aceitou o login pelo LinkedIn" in r.motivo_fim, r.motivo_fim
    assert linhas["vaga_recusa.html"]["status"] == "nova"


# --- a Gupy fecha a aba / volta para o login depois do LinkedIn (17/11) ----------------------------------

def test_volta_para_o_login_depois_do_linkedin_mas_a_sessao_vale_reabre_a_vaga(tmp_path, pdf, site):
    cfg = ConfigAutomacao(modo="simulacao", entrar_com_linkedin=True)
    banco = _banco_com(tmp_path, f"{site.url}/vaga_sessao.html")
    _, linhas, eventos = _rodar(tmp_path, pdf, banco, cfg)
    assert linhas["vaga_sessao.html"]["status"] == "simulada", linhas["vaga_sessao.html"]["motivo"]
    assert eventos == []


def test_gupy_fecha_a_aba_da_vaga_o_bot_reabre_e_o_navegador_nao_fecha(tmp_path, pdf, site, monkeypatch):
    """Caso real: 'chegou ao formulário e fechou sozinha' (window.close da Gupy). Com a aba âncora o navegador
    continua aberto, e o bot reabre a vaga."""
    original = piloto._esperar_render
    feito = []

    def fecha_uma_vez(pg, *a, **k):
        if "passo1.html" in pg.url and not feito:
            feito.append(True)
            pg.close()  # a Gupy fechando a aba
            return False
        return original(pg, *a, **k)

    monkeypatch.setattr(piloto, "_esperar_render", fecha_uma_vez)
    banco = _banco_com(tmp_path, f"{site.url}/vaga_curta.html", f"{site.url}/sem_botao.html")
    r, linhas, eventos = _rodar(tmp_path, pdf, banco, SIM)
    assert feito
    assert linhas["vaga_curta.html"]["status"] == "simulada", linhas["vaga_curta.html"]["motivo"]
    assert linhas["sem_botao.html"]["status"] == "pulada"  # a rodada seguiu: o navegador não fechou
    assert r.motivo_fim == ""


# --- login da Gupy digitado a partir do chaveiro (decisão do Thiago, 17/11) ---------------------------------

DIGITAR = ConfigAutomacao(modo="simulacao", digitar_senha_gupy=True, entrar_com_linkedin=True)
CRED = lambda: ("fulano@example.com", "segredo-ficticio")  # noqa: E731 (fictícios)


def _proibidos(site):
    return [p for p in site.pedidos
            if p.startswith(("/errado.html", "/entrar_sem_senha.html", "/linkedin_clicado.html"))]


@pytest.mark.parametrize("caso", ["ok", "emailprimeiro"])
def test_digita_login_e_senha_do_chaveiro_e_segue(tmp_path, pdf, site, caso):
    site.pedidos.clear()
    banco = _banco_com(tmp_path, f"{site.url}/vaga_senha_{caso}.html")
    _, linhas, eventos = _rodar(tmp_path, pdf, banco, DIGITAR, credenciais=CRED)
    assert linhas[f"vaga_senha_{caso}.html"]["status"] == "simulada", linhas[f"vaga_senha_{caso}.html"]["motivo"]
    assert eventos == [] and _proibidos(site) == []  # valores certos; não usou LinkedIn nem 'sem senha'


@pytest.mark.parametrize("caso,cred,trecho", [
    ("desabilitado", CRED, "não liberou o botão"),
    ("recusada", CRED, "continuou pedindo login"),
    ("codigo", CRED, "sem campo de e-mail/senha"),
    ("ok", lambda: None, "não está no chaveiro"),
])
def test_login_digitado_para_sem_insistir(tmp_path, pdf, site, caso, cred, trecho):
    site.pedidos.clear()
    banco = _banco_com(tmp_path, f"{site.url}/vaga_senha_{caso}.html")
    r, linhas, eventos = _rodar(tmp_path, pdf, banco, DIGITAR, credenciais=cred)
    assert eventos == ["login"] and trecho in r.motivo_fim, r.motivo_fim
    assert linhas[f"vaga_senha_{caso}.html"]["status"] == "nova"
    assert _proibidos(site) == []
    if caso == "recusada":  # digitou a senha UMA vez só
        assert sum(p.startswith("/senha_recusada/candidates/signin.html") for p in site.pedidos) == 2


def test_chaveiro_trancado_para_sem_vazar(tmp_path, pdf, site):
    def trancado():
        raise RuntimeError("segredo-ficticio")  # nem a mensagem do erro pode aparecer

    banco = _banco_com(tmp_path, f"{site.url}/vaga_senha_ok.html")
    r, _, eventos = _rodar(tmp_path, pdf, banco, DIGITAR, credenciais=trancado)
    assert eventos == ["login"] and "chaveiro" in r.motivo_fim and "segredo" not in r.motivo_fim


def test_sem_a_opcao_nunca_digita(tmp_path, pdf, site):
    chamadas = []
    banco = _banco_com(tmp_path, f"{site.url}/vaga_senha_ok.html")
    r, _, _ = _rodar(tmp_path, pdf, banco, SIM, credenciais=lambda: chamadas.append(1) or CRED())
    assert chamadas == [] and "pediu login" in r.motivo_fim





# --- comando senha-gupy (chaveiro falso: nunca o real nos testes) ------------------------------------------

class _ChaveiroFalso:
    def __init__(self):
        self.d = {}

    def set_password(self, s, u, p):
        self.d[(s, u)] = p

    def get_password(self, s, u):
        return self.d.get((s, u))

    def delete_password(self, s, u):
        del self.d[(s, u)]


def _cli_senha(args, kr, respostas, capsys):
    import argparse

    it = iter(respostas)
    ns = argparse.Namespace(apagar="--apagar" in args, verificar="--verificar" in args, keyring=kr,
                            ler_segredo=lambda prompt="": next(it))
    codigo = cli.cmd_senha_gupy(ns)
    return codigo, capsys.readouterr()


def test_senha_gupy_guarda_verifica_e_apaga_sem_mostrar(capsys):
    from bot_vagas import credenciais

    kr = _ChaveiroFalso()
    codigo, saida = _cli_senha([], kr, ["fulano@example.com", "segredo-ficticio", "segredo-ficticio"], capsys)
    assert codigo == 0 and credenciais.carregar(kr) == ("fulano@example.com", "segredo-ficticio")
    assert "segredo" not in saida.out + saida.err and "fulano" not in saida.out + saida.err
    assert _cli_senha(["--verificar"], kr, [], capsys)[0] == 0
    assert _cli_senha(["--apagar"], kr, [], capsys)[0] == 0
    assert credenciais.carregar(kr) is None and _cli_senha(["--verificar"], kr, [], capsys)[0] == 1


def test_senha_gupy_recusa_confirmacao_diferente(capsys):
    from bot_vagas import credenciais

    kr = _ChaveiroFalso()
    codigo, _ = _cli_senha([], kr, ["fulano@example.com", "a", "b"], capsys)
    assert codigo == 2 and credenciais.carregar(kr) is None
