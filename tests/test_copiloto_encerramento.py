"""Copiloto: encerramento limpo (janela fechada, Ctrl+C, SIGTERM, PARAR), perfil em uso, log de depuração
e aviso de login. Tudo contra FIXTURES em 127.0.0.1, Chromium headless e perfil temporário; nunca a Gupy real."""

import json
import os
import shutil
import signal
import socket
import stat
import subprocess
import sys
import textwrap
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from bot_vagas.banco import Banco
from bot_vagas.copiloto import LogCampos, perfil_em_uso
from bot_vagas.notificacao import ConfigNotificacao, Notificador
from bot_vagas.respostas import valores_sensiveis

from test_candidatar import (  # noqa: F401  (fixtures)
    FIXTURES, RESPOSTAS_TESTE, ClienteFalhaSeChamado, _argv, _cli, _copiloto, ambiente, navegador, pagina, pdf, servidor,
)

RAIZ = Path(__file__).resolve().parents[1]
SENSIVEIS = valores_sensiveis(RESPOSTAS_TESTE) + ["2500", "31/01/1999", "06/2029"]


def _fetch(u):
    return u, (FIXTURES / "gupy_form_fixture.html").read_text(encoding="utf-8")


def _candidatar(ambiente, url, pdf, cfg_extra=None, *extra):
    cfg = {**ambiente.cfg, "pasta_logs": ambiente.raiz / "logs", **(cfg_extra or {})}
    return _cli(_argv(ambiente, "candidatar", url, "--pdf", str(pdf), "--empresa", "Acme", "--cargo", "Dev",
                      "--saida", str(ambiente.saida), *extra),
                copiloto_cfg=cfg, client_factory=ClienteFalhaSeChamado, fetch=_fetch)


def _assistencia(ambiente):
    with Banco(ambiente.banco) as b:
        rows = b.listar("em_candidatura")
        assert len(rows) == 1
        return json.loads(rows[0]["detalhes"])["assistencia"]


# --- 1a: fechar a janela / Ctrl+C / SIGTERM ------------------------------------------------

def test_fechar_a_aba_encerra_imprime_resumo_e_grava(ambiente, servidor, pdf, capsys):
    ciclos = []

    def fechar_tudo(ctx):
        ciclos.append(1)
        if len(ciclos) == 2:
            for pg in list(ctx.pages):
                pg.close()  # como o usuário fechando a janela

    inicio = time.monotonic()
    codigo = _candidatar(ambiente, f"{servidor}/gupy_form_fixture.html", pdf,
                         {"max_passos": None, "intervalo": 0.05, "ao_ciclo": fechar_tudo})
    out = capsys.readouterr().out
    assert codigo == 0, out
    assert time.monotonic() - inicio < 30
    assert "RESUMO DA ASSISTÊNCIA" in out and "encerramento: janela fechada" in out
    a = _assistencia(ambiente)
    assert a["encerramento"] == "janela fechada" and a["pdf_anexado"] is True
    with Banco(ambiente.banco) as b:
        assert b.contar_por_status() == {"em_candidatura": 1}  # nunca marca 'enviada' sozinho


def test_fechar_o_navegador_inteiro_encerra(ambiente, servidor, pdf, capsys):
    codigo = _candidatar(ambiente, f"{servidor}/gupy_form_fixture.html", pdf,
                         {"max_passos": None, "intervalo": 0.05, "ao_ciclo": lambda ctx: ctx.close()})
    out = capsys.readouterr().out
    assert codigo == 0, out
    assert "RESUMO DA ASSISTÊNCIA" in out
    assert _assistencia(ambiente)["encerramento"] == "janela fechada"


def test_ctrl_c_durante_a_assistencia_encerra_limpo(ambiente, servidor, pdf, capsys):
    def ctrl_c(ctx):
        raise KeyboardInterrupt

    codigo = _candidatar(ambiente, f"{servidor}/gupy_form_fixture.html", pdf,
                         {"max_passos": None, "ao_ciclo": ctrl_c})
    cap = capsys.readouterr()
    assert codigo == 0, cap.out
    assert "Traceback" not in cap.out + cap.err
    assert "encerramento: interrompido (Ctrl+C)" in cap.out and "bot-vagas marcar" in cap.out
    a = _assistencia(ambiente)
    assert a["encerramento"] == "interrompido (Ctrl+C)" and a["preenchidos"]


_SCRIPT = textwrap.dedent("""
    import sys
    from pathlib import Path
    from bot_vagas import cli
    banco, resp, saida, pdf, perfil_nav, logs, url, fixture = sys.argv[1:9]
    cfg = {"headless": True, "permitir_loopback": True, "max_passos": None, "intervalo": 0.2,
           "pasta_perfil": Path(perfil_nav), "pasta_logs": Path(logs)}
    html = Path(fixture).read_text(encoding="utf-8")
    sys.exit(cli.main(["candidatar", url, "--pdf", pdf, "--empresa", "Acme", "--cargo", "Dev", "--banco", banco,
                       "--respostas", resp, "--saida", saida], copiloto_cfg=cfg, fetch=lambda u: (u, html)))
""")


@pytest.mark.parametrize("sinal,esperado", [(signal.SIGINT, "interrompido (Ctrl+C)"),
                                            (signal.SIGTERM, "interrompido (SIGTERM)")])
def test_sinal_real_no_grupo_de_processos(ambiente, servidor, pdf, tmp_path, sinal, esperado):
    """Como no terminal: o sinal vai para o GRUPO inteiro (python, driver do Playwright e Chromium)."""
    script = tmp_path / "rodar_candidatar.py"
    script.write_text(_SCRIPT, encoding="utf-8")
    env = {**os.environ, "PYTHONPATH": str(RAIZ / "src"), "PYTHONUNBUFFERED": "1"}
    proc = subprocess.Popen(
        [sys.executable, str(script), str(ambiente.banco), str(ambiente.respostas), str(ambiente.saida), str(pdf),
         str(ambiente.perfil_nav), str(tmp_path / "logs"), f"{servidor}/gupy_form_fixture.html",
         str(FIXTURES / "gupy_form_fixture.html")],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env, start_new_session=True)
    saida = []
    try:
        limite = time.monotonic() + 60
        for linha in proc.stdout:  # espera o copiloto começar a preencher
            saida.append(linha)
            if "preencheu:" in linha or time.monotonic() > limite:
                break
        time.sleep(0.5)
        os.killpg(proc.pid, sinal)
        resto, err = proc.communicate(timeout=60)
    finally:
        if proc.poll() is None:
            os.killpg(proc.pid, signal.SIGKILL)
    out = "".join(saida) + resto
    assert proc.returncode == 0, (out, err)
    assert "Traceback" not in out + err, err
    assert "RESUMO DA ASSISTÊNCIA" in out and f"encerramento: {esperado}" in out
    assert _assistencia(ambiente)["encerramento"] == esperado
    for s in SENSIVEIS:
        assert s not in out, s


# --- 1b: perfil do navegador já em uso --------------------------------------------------------

def _travar_perfil(pasta: Path):
    pasta.mkdir(parents=True, exist_ok=True)
    os.symlink(f"{socket.gethostname()}-{os.getpid()}", pasta / "SingletonLock")  # processo vivo: este


def test_candidatar_com_perfil_em_uso_mensagem_clara(ambiente, servidor, pdf, capsys):
    _travar_perfil(ambiente.perfil_nav)
    codigo = _candidatar(ambiente, f"{servidor}/gupy_form_fixture.html", pdf)
    cap = capsys.readouterr()
    assert codigo == 3
    assert "Já existe um navegador usando o perfil do bot" in cap.err
    assert "Traceback" not in cap.out + cap.err and "RESUMO" not in cap.out
    with Banco(ambiente.banco) as b:
        assert "em_candidatura" not in b.contar_por_status()  # status não ficou preso


def test_login_gupy_com_perfil_em_uso_mensagem_clara(ambiente, capsys):
    _travar_perfil(ambiente.perfil_nav)
    codigo = _cli(["login-gupy"], copiloto_cfg={**ambiente.cfg, "max_segundos": 0.5})
    cap = capsys.readouterr()
    assert codigo == 3
    assert "Já existe um navegador usando o perfil do bot" in cap.err and "Traceback" not in cap.err


def test_lock_de_processo_morto_ou_de_outra_maquina_nao_conta(tmp_path):
    a = tmp_path / "a"
    a.mkdir()
    os.symlink(f"{socket.gethostname()}-999999999", a / "SingletonLock")
    assert not perfil_em_uso(a)
    b = tmp_path / "b"
    b.mkdir()
    os.symlink(f"outra-maquina-{os.getpid()}", b / "SingletonLock")
    assert not perfil_em_uso(b)
    assert not perfil_em_uso(tmp_path / "nao-existe")


@pytest.mark.skipif(not shutil.which("xvfb-run"), reason="precisa de xvfb-run para um Chromium visível")
def test_perfil_realmente_aberto_por_outro_chromium(ambiente, servidor, pdf, capsys, tmp_path):
    """Um Chromium VISÍVEL de verdade (outro processo, sob Xvfb) segurando o perfil, como um login-gupy
    esquecido aberto. (O headless não trava o perfil; em produção o navegador é sempre visível.)"""
    ambiente.perfil_nav.mkdir(parents=True, exist_ok=True)
    script = tmp_path / "segurar.py"
    script.write_text(textwrap.dedent("""
        import sys, time
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            ctx = p.chromium.launch_persistent_context(sys.argv[1], headless=False)
            print("aberto", flush=True)
            time.sleep(60)
    """), encoding="utf-8")
    proc = subprocess.Popen(["xvfb-run", "-a", sys.executable, str(script), str(ambiente.perfil_nav)],
                            stdout=subprocess.PIPE, text=True, start_new_session=True)
    try:
        assert proc.stdout.readline().strip() == "aberto"
        assert perfil_em_uso(ambiente.perfil_nav)
        codigo = _candidatar(ambiente, f"{servidor}/gupy_form_fixture.html", pdf)
        cap = capsys.readouterr()
        assert codigo == 3, cap.out + cap.err
        assert "Já existe um navegador usando o perfil do bot" in cap.err and "Traceback" not in cap.err
    finally:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.wait(10)


def test_erro_de_perfil_em_uso_no_launch_vira_mensagem_clara(tmp_path):
    """Corrida: o lock aparece entre a checagem e o launch -> o erro do Playwright vira PerfilEmUso."""
    from playwright.sync_api import Error as ErroPW

    from bot_vagas.copiloto import PerfilEmUso, abrir_contexto_persistente

    class _Chromium:
        def launch_persistent_context(self, **kw):
            raise ErroPW("BrowserType.launch_persistent_context: Opening in existing browser session. This usually "
                         "means that the profile is already in use by another instance of Chromium.")

    with pytest.raises(PerfilEmUso, match="Já existe um navegador"):
        abrir_contexto_persistente(SimpleNamespace(chromium=_Chromium()), tmp_path)

    class _Outro:
        def launch_persistent_context(self, **kw):
            raise ErroPW("Executable doesn't exist")

    with pytest.raises(ErroPW):  # outros erros não são mascarados
        abrir_contexto_persistente(SimpleNamespace(chromium=_Outro()), tmp_path)


def test_campos_pulados_nao_geram_falha(pagina, servidor, pdf):
    """Regressão: `_pular` não existia e cada campo pulado virava AttributeError + aviso repetido."""
    pagina.goto(f"{servidor}/gupy_form_fixture.html")
    cp = _copiloto(pdf)
    for _ in range(3):
        cp.passo(pagina)
    assert not [a for a in cp.registro.avisos if "falha ao tratar" in a], cp.registro.avisos


# --- 1c: log local de depuração --------------------------------------------------------------

def test_log_de_depuracao_0600_sem_valores_pessoais(ambiente, servidor, pdf, capsys):
    assert _candidatar(ambiente, f"{servidor}/gupy_form_fixture.html", pdf) == 0
    out = capsys.readouterr().out
    pasta = ambiente.raiz / "logs"
    arquivos = list(pasta.glob("candidatar-*.jsonl"))
    assert len(arquivos) == 1 and str(arquivos[0]) in out
    assert stat.S_IMODE(pasta.stat().st_mode) == 0o700
    assert stat.S_IMODE(arquivos[0].stat().st_mode) == 0o600
    texto = arquivos[0].read_text(encoding="utf-8")
    registros = [json.loads(l) for l in texto.splitlines()]
    campos = [r for r in registros if r["evento"] == "campo"]
    assert campos and {"tipo", "categoria", "resultado", "motivo", "textos"} <= set(campos[0])
    assert any(r["resultado"] == "preenchido" for r in campos)
    assert any(r["resultado"] == "pendente" for r in campos)
    assert any(r["categoria"].startswith("proibido: consentimento") for r in campos)
    assert registros[-1]["evento"] == "fim" and registros[-1]["encerramento"]
    for s in SENSIVEIS:
        assert s not in texto, s
    for proibido in ("cookie", "password\":", "senha\":"):
        assert proibido not in texto.lower()
    assert _assistencia(ambiente)["log_campos"] == str(arquivos[0])


def test_sem_log_nao_cria_arquivo(ambiente, servidor, pdf, capsys):
    assert _candidatar(ambiente, f"{servidor}/gupy_form_fixture.html", pdf, None, "--sem-log") == 0
    assert "log de depuração" not in capsys.readouterr().out
    assert not list((ambiente.raiz / "logs").glob("*.jsonl"))


def test_log_redige_valores_e_query(tmp_path):
    log = LogCampos(tmp_path / "l", RESPOSTAS_TESTE)
    log.campo("https://x.gupy.io/job?token=SEGREDO&email=a@b.c", {"id": "bv1", "tag": "input", "tipo": "text",
              "label": "Cidade (ex.: Maceió, AL)", "proximo": "Você mora em Maceió?"}, "localizacao.cidade", "preenchido", "")
    log.evento("fim", encerramento="x")
    log.fechar()
    texto = log.caminho.read_text(encoding="utf-8")
    assert "Maceió" not in texto and "SEGREDO" not in texto and "a@b.c" not in texto
    assert "[omitido]" in texto and "https://x.gupy.io/job" in texto


# --- 1d: aviso de login uma vez por mudança de página -----------------------------------------

def test_aviso_de_login_uma_vez_por_mudanca_de_pagina(pagina, servidor, pdf):
    login, form = f"{servidor}/gupy_login_fixture.html", f"{servidor}/gupy_form_fixture.html"
    cp = _copiloto(pdf)
    pagina.goto(login)
    for _ in range(5):
        cp.passo(pagina)
    assert sum("PRECISA DE LOGIN" in m for m in cp._mensagens) == 1
    pagina.goto(form)
    cp.passo(pagina)
    pagina.goto(login + "?redirect=abc")  # voltou ao login (query diferente = mesma tela)
    for _ in range(3):
        cp.passo(pagina)
    assert sum("PRECISA DE LOGIN" in m for m in cp._mensagens) == 2
    assert cp.registro.avisos.count("tela de login detectada; aguardando o usuário") == 2


def test_aviso_de_login_com_duas_abas_nao_repete(navegador, servidor, pdf):
    ctx = navegador.new_context()
    try:
        a, b = ctx.new_page(), ctx.new_page()
        a.goto(f"{servidor}/gupy_login_fixture.html")
        b.goto(f"{servidor}/gupy_form_fixture.html")
        cp = _copiloto(pdf)
        for _ in range(4):
            cp.passo(a)
            cp.passo(b)
        assert sum("PRECISA DE LOGIN" in m for m in cp._mensagens) == 1
    finally:
        ctx.close()


def test_login_notifica_uma_vez_sem_dados(pagina, servidor, pdf):
    enviados = []
    cp = _copiloto(pdf)
    cp.notificador = Notificador(ConfigNotificacao(topico="topico-teste-123"),
                                 enviar=lambda url, corpo, cab: enviados.append((url, corpo, cab)))
    pagina.goto(f"{servidor}/gupy_login_fixture.html")
    cp.passo(pagina)
    pagina.goto(f"{servidor}/gupy_form_fixture.html")
    cp.passo(pagina)
    pagina.goto(f"{servidor}/gupy_login_fixture.html")
    cp.passo(pagina)
    assert len(enviados) == 1  # uma notificação por execução, mesmo com 2 avisos
    url, corpo, cab = enviados[0]
    assert url == "https://ntfy.sh/topico-teste-123"
    texto = corpo.decode() + json.dumps(cab)
    for s in SENSIVEIS + ["127.0.0.1", "Acme", "gupy"]:
        assert s not in texto, s
