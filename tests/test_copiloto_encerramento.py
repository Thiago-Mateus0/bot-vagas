"""Copiloto: encerramento limpo (janela fechada, Ctrl+C, SIGTERM, PARAR), perfil do navegador em uso, log local de
depuração sem valores pessoais e aviso "PRECISA DE LOGIN" uma vez por mudança de página.

Tudo contra fixtures em 127.0.0.1, Chromium headless e perfis TEMPORÁRIOS. Nunca a Gupy real nem o perfil real.
"""

import json
import os
import signal
import socket
import stat
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest
import yaml

from bot_vagas import copiloto as mod
from bot_vagas.banco import Banco
from bot_vagas.copiloto import (MENSAGEM_PERFIL_EM_USO, Copiloto, LogCampos, perfil_em_uso)
from bot_vagas.respostas import valores_sensiveis

from test_candidatar import (ALVOS_TESTE, FIXTURES, _argv, _cli, ambiente, navegador, pdf,  # noqa: F401 (fixtures)
                             servidor)
from test_preparar import RESPOSTAS_TESTE, ClienteFalhaSeChamado

RAIZ = Path(__file__).resolve().parents[1]
VALORES_FORMATADOS = ["1850", "31/01/1999", "1999-01-31", "12/2031", "2031-12"]


def _todos_sensiveis():
    return valores_sensiveis(RESPOSTAS_TESTE) + VALORES_FORMATADOS


def _assistencia(banco_path):
    with Banco(banco_path) as b:
        rows = b.listar("em_candidatura")
        assert len(rows) == 1, [dict(r) for r in b.listar()]
        return json.loads(rows[0]["detalhes"])["assistencia"]


def _cfg_sem_limite(amb, **extra):
    cfg = dict(amb.cfg, max_passos=None, **extra)  # sem limite: só termina se detectar o fechamento
    return cfg


def _rodar_candidatar(amb, servidor, pdf, capsys, *extra, cfg=None):
    url = f"{servidor}/gupy_form_fixture.html"
    codigo = _cli(_argv(amb, "candidatar", url, "--pdf", str(pdf), "--empresa", "Acme", "--cargo", "Dev",
                        "--saida", str(amb.saida), *extra),
                  copiloto_cfg=cfg or _cfg_sem_limite(amb), client_factory=ClienteFalhaSeChamado)
    cap = capsys.readouterr()
    return codigo, cap.out, cap.err


def _depois_do_primeiro_passo(monkeypatch, acao):
    """Embrulha Copiloto.passo: o primeiro passo roda normalmente (preenche) e então `acao(pagina)` simula o
    usuário (fechar aba/janela, Ctrl+C...)."""
    original = Copiloto.passo
    chamadas = {"n": 0}

    def passo(self, pagina):
        r = original(self, pagina)
        chamadas["n"] += 1
        if chamadas["n"] == 1:
            acao(pagina)
        return r

    monkeypatch.setattr(Copiloto, "passo", passo)
    return chamadas


# --- 1a: fechar a janela / Ctrl+C encerra limpo, imprime resumo e grava ----------------------------------

def test_fechar_a_aba_encerra_imprime_resumo_e_grava(ambiente, servidor, pdf, capsys, monkeypatch):
    _depois_do_primeiro_passo(monkeypatch, lambda pg: pg.close())
    codigo, out, err = _rodar_candidatar(ambiente, servidor, pdf, capsys)
    assert codigo == 0, out + err
    assert "RESUMO DA ASSISTÊNCIA" in out and "encerramento: janela fechada" in out
    assert "Traceback" not in out + err
    a = _assistencia(ambiente.banco)
    assert a["encerramento"] == "janela fechada" and a["pdf_anexado"] is True and a["preenchidos"]
    with Banco(ambiente.banco) as b:
        assert b.contar_por_status() == {"em_candidatura": 1}  # NUNCA vira 'enviada' sozinho


def test_fechar_o_navegador_inteiro_encerra_e_grava(ambiente, servidor, pdf, capsys, monkeypatch):
    _depois_do_primeiro_passo(monkeypatch, lambda pg: pg.context.close())
    codigo, out, err = _rodar_candidatar(ambiente, servidor, pdf, capsys)
    assert codigo == 0, out + err
    assert "RESUMO DA ASSISTÊNCIA" in out and "Traceback" not in out + err
    assert _assistencia(ambiente.banco)["encerramento"] in ("janela fechada", "navegador encerrado")


def test_keyboardinterrupt_no_meio_imprime_resumo_e_grava(ambiente, servidor, pdf, capsys, monkeypatch):
    def ctrl_c(_pg):
        raise KeyboardInterrupt

    _depois_do_primeiro_passo(monkeypatch, ctrl_c)
    codigo, out, err = _rodar_candidatar(ambiente, servidor, pdf, capsys)
    assert codigo == 130
    assert "RESUMO DA ASSISTÊNCIA" in out and "interrompido (Ctrl+C)" in out
    assert "Traceback" not in out + err
    a = _assistencia(ambiente.banco)
    assert a["encerramento"] == "interrompido (Ctrl+C)" and a["preenchidos"]


def test_erro_inesperado_tambem_grava_resumo_sem_traceback(ambiente, servidor, pdf, capsys, monkeypatch):
    def quebra(_pg):
        raise RuntimeError("falha simulada")

    _depois_do_primeiro_passo(monkeypatch, quebra)
    codigo, out, err = _rodar_candidatar(ambiente, servidor, pdf, capsys)
    assert codigo == 1
    assert "RESUMO DA ASSISTÊNCIA" in out and "ERRO inesperado" in err and "Traceback" not in out + err
    assert _assistencia(ambiente.banco)["encerramento"] == "erro: RuntimeError"


_FILHO = textwrap.dedent("""
    import sys, threading, os
    from functools import partial
    from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
    class H(SimpleHTTPRequestHandler):
        def log_message(self, *a): pass
    srv = ThreadingHTTPServer(("127.0.0.1", 0), partial(H, directory=sys.argv[1]))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    from bot_vagas import cli
    tmp = sys.argv[2]
    cfg = {"headless": True, "permitir_loopback": True, "max_passos": None, "intervalo": 0.1,
           "pasta_perfil": tmp + "/perfil-nav", "pasta_logs": tmp + "/logs"}
    url = f"http://127.0.0.1:{srv.server_address[1]}/gupy_form_fixture.html"
    sys.exit(cli.main(["candidatar", url, "--pdf", sys.argv[3], "--empresa", "Acme", "--cargo", "Dev",
                       "--banco", tmp + "/t.db", "--respostas", tmp + "/respostas.yaml", "--saida", tmp + "/saida"],
                      copiloto_cfg=cfg))
""")


@pytest.mark.parametrize("sinal,motivo", [(signal.SIGINT, "interrompido (Ctrl+C)"),
                                          (signal.SIGTERM, "interrompido (SIGTERM)")])
def test_sinal_real_no_grupo_de_processos_encerra_limpo(tmp_path, pdf, sinal, motivo):
    """Como no terminal: o sinal vai para o GRUPO inteiro (Python + driver do Playwright + Chromium)."""
    (tmp_path / "respostas.yaml").write_text(yaml.safe_dump(RESPOSTAS_TESTE, allow_unicode=True), encoding="utf-8")
    script = tmp_path / "filho.py"
    script.write_text(_FILHO, encoding="utf-8")
    env = dict(os.environ, PYTHONPATH=str(RAIZ / "src"), PYTHONUNBUFFERED="1")
    if not (RAIZ / "perfil.yaml").exists():
        env["BOT_VAGAS_PERFIL"] = str(RAIZ / "perfil.example.yaml")
    proc = subprocess.Popen([sys.executable, str(script), str(FIXTURES), str(tmp_path), str(pdf)], env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
    linhas = []
    try:
        limite = time.monotonic() + 60
        while time.monotonic() < limite:
            linha = proc.stdout.readline()
            if not linha:
                break
            linhas.append(linha)
            if "preencheu:" in linha:
                break
        assert any("preencheu:" in l for l in linhas), "".join(linhas) + proc.stderr.read()
        time.sleep(0.3)
        os.killpg(proc.pid, sinal)
        out, err = proc.communicate(timeout=30)
    finally:
        if proc.poll() is None:
            os.killpg(proc.pid, signal.SIGKILL)
    out = "".join(linhas) + out
    assert proc.returncode == 130, out + err
    assert "RESUMO DA ASSISTÊNCIA" in out and f"encerramento: {motivo}" in out
    assert "Traceback" not in err, err
    a = _assistencia(tmp_path / "t.db")
    assert a["encerramento"] == motivo and a["pdf_anexado"] is True
    for s in _todos_sensiveis():
        assert s not in out, s


# --- 1b: perfil do navegador em uso ----------------------------------------------------------------------

def _lock(pasta: Path, alvo: str):
    pasta.mkdir(parents=True, exist_ok=True)
    os.symlink(alvo, pasta / "SingletonLock")


def test_perfil_em_uso_detecta_lock_vivo_e_ignora_lock_velho(tmp_path):
    vivo, morto, outro = tmp_path / "vivo", tmp_path / "morto", tmp_path / "outro"
    _lock(vivo, f"{socket.gethostname()}-{os.getpid()}")
    _lock(morto, f"{socket.gethostname()}-999999999")
    _lock(outro, f"outra-maquina-{os.getpid()}")
    assert perfil_em_uso(vivo) is True
    assert perfil_em_uso(morto) is False
    assert perfil_em_uso(outro) is False
    assert perfil_em_uso(tmp_path / "nao-existe") is False


@pytest.mark.parametrize("comando", ["candidatar", "login-gupy"])
def test_perfil_em_uso_mensagem_clara_sem_traceback(ambiente, servidor, pdf, capsys, comando):
    _lock(ambiente.perfil_nav, f"{socket.gethostname()}-{os.getpid()}")
    if comando == "candidatar":
        argv = _argv(ambiente, "candidatar", f"{servidor}/gupy_form_fixture.html", "--pdf", str(pdf),
                     "--empresa", "Acme", "--cargo", "Dev")
    else:
        argv = ["login-gupy"]
    codigo = _cli(argv, copiloto_cfg=ambiente.cfg)
    cap = capsys.readouterr()
    assert codigo == 3
    assert MENSAGEM_PERFIL_EM_USO in cap.err and cap.err.count("\n") <= 2
    assert "Traceback" not in cap.out + cap.err
    if comando == "candidatar":
        with Banco(ambiente.banco) as b:
            assert b.contar_por_status() == {}  # nada foi preparado nem mudou de status


_SEGURA_PERFIL = textwrap.dedent("""
    import sys
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(sys.argv[1], headless=True, channel="chromium")
        print("PRONTO", flush=True)
        sys.stdin.read()
        ctx.close()
""")


@pytest.fixture
def perfil_aberto_por_outro_chromium(tmp_path):
    """Um Chromium DE VERDADE (outro processo) segurando o perfil temporário, como um login-gupy esquecido."""
    pasta = tmp_path / "perfil-ocupado"
    script = tmp_path / "segura.py"
    script.write_text(_SEGURA_PERFIL, encoding="utf-8")
    proc = subprocess.Popen([sys.executable, str(script), str(pasta)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True)
    try:
        assert proc.stdout.readline().strip() == "PRONTO", proc.stderr.read()
        yield pasta
    finally:
        proc.stdin.close()
        try:
            proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            proc.kill()


@pytest.mark.parametrize("pular_checagem_previa", [False, True])
def test_perfil_aberto_por_chromium_real(perfil_aberto_por_outro_chromium, ambiente, servidor, pdf, capsys,
                                         monkeypatch, pular_checagem_previa):
    """Sem a checagem prévia (SingletonLock), o próprio erro de lançamento do Chromium vira a mesma mensagem."""
    if pular_checagem_previa:
        monkeypatch.setattr(mod, "perfil_em_uso", lambda pasta: False)
    cfg = dict(ambiente.cfg, pasta_perfil=perfil_aberto_por_outro_chromium, canal="chromium")
    for argv in (["login-gupy"], _argv(ambiente, "candidatar", f"{servidor}/gupy_form_fixture.html", "--pdf", str(pdf),
                                       "--empresa", "Acme", "--cargo", "Dev", "--saida", str(ambiente.saida))):
        codigo = _cli(argv, copiloto_cfg=cfg)
        cap = capsys.readouterr()
        assert codigo == 3, cap.out + cap.err
        assert MENSAGEM_PERFIL_EM_USO in cap.err
        assert "Traceback" not in cap.out + cap.err and "ProcessSingleton" not in cap.out + cap.err


# --- 1c: log local de depuração -----------------------------------------------------------------------

def test_log_de_campos_0600_sem_valores_pessoais(navegador, servidor, pdf, tmp_path):
    ctx = navegador.new_context()
    pg = ctx.new_page()
    try:
        pasta = tmp_path / "logs"
        log = LogCampos(pasta, RESPOSTAS_TESTE)
        cp = Copiloto(RESPOSTAS_TESTE, pdf, alvos=ALVOS_TESTE, log=lambda *a, **k: None, log_campos=log)
        pg.goto(f"{servidor}/gupy_form_fixture.html")
        cp.passo(pg)
        cp.passo(pg)  # segundo passo não duplica registros
        # tela de login com dados FICTÍCIOS digitados pelo "usuário"
        pg.goto(f"{servidor}/gupy_login_fixture.html")
        pg.fill("#email", "fulano.ficticio@exemplo.com")
        pg.fill("#senha", "SenhaFicticia#123")
        cp.passo(pg)
        log.fechar()
    finally:
        ctx.close()
    assert stat.S_IMODE(pasta.stat().st_mode) == 0o700
    assert stat.S_IMODE(log.caminho.stat().st_mode) == 0o600
    assert log.caminho.name.startswith("candidatar-") and log.caminho.suffix == ".jsonl"
    bruto = log.caminho.read_text(encoding="utf-8")
    for s in _todos_sensiveis() + ["fulano.ficticio@exemplo.com", "SenhaFicticia#123"]:
        assert s.lower() not in bruto.lower(), s
    regs = [json.loads(l) for l in bruto.splitlines()]
    campos = [r for r in regs if r["evento"] == "campo"]
    assert all("value" not in r and "valor" not in r for r in regs)
    # um registro por campo visto, com rótulo/tipo, categoria, resultado e motivo
    ids = [(r["url"], r["textos"].get("elid") or r["textos"].get("name"), r["textos"].get("label")) for r in campos]
    assert len(ids) == len(set(ids))
    por_id = {r["textos"].get("elid"): r for r in campos}
    assert por_id["cidade"]["categoria"] == "localizacao.cidade" and por_id["cidade"]["resultado"] == "preenchido"
    assert por_id["lgpd"]["categoria"] == "proibido: consentimento" and por_id["lgpd"]["resultado"] == "pendente"
    assert por_id["linkedin"]["categoria"] == "não reconhecido" and por_id["linkedin"]["motivo"]
    assert por_id["estado"]["ja_tinha_valor"] is True and por_id["estado"]["resultado"] == "pulou"
    assert por_id["viajar"]["resultado"] == "pendente" and "nulo" in por_id["viajar"]["motivo"]
    telas = [r for r in regs if r["evento"] == "tela"]
    assert any(t["tela_de_login"] for t in telas) and all("?" not in t["url"] for t in telas)


def test_log_redige_nome_email_telefone_e_query(tmp_path):
    log = LogCampos(tmp_path / "l", {"localizacao": {"cidade": "Cidadezinha"}},
                    extras_sensiveis=["Fulano Ficticio da Silva", "Ficticio"])
    c = {"id": "bv1", "tag": "input", "tipo": "text",
         "label": "Olá, Fulano Ficticio da Silva! Cidade (Cidadezinha)",
         "proximo": "Contato: fulano@exemplo.com.br ou (82) 99999-1234, CPF 123.456.789-00",
         "opcoes": [{"text": "Cidadezinha"}, {"text": "Outra"}]}
    log.campo("https://x.gupy.io/candidatura?token=SEGREDO&email=a@b.co", c, "localizacao.cidade", "preenchido", "ok")
    log.fechar()
    bruto = log.caminho.read_text(encoding="utf-8")
    for s in ("Fulano", "Ficticio", "Cidadezinha", "fulano@exemplo.com.br", "99999-1234", "123.456.789-00",
              "SEGREDO", "token", "a@b.co"):
        assert s not in bruto, s
    assert "Outra" in bruto and "[omitido]" in bruto


def test_cli_grava_log_e_sem_log_desliga(ambiente, servidor, pdf, capsys, tmp_path):
    pasta_logs = tmp_path / "logs-cli"
    cfg = dict(ambiente.cfg, pasta_logs=pasta_logs)
    codigo, out, err = _rodar_candidatar(ambiente, servidor, pdf, capsys, cfg=cfg)
    assert codigo == 0, out + err
    arquivos = list(pasta_logs.glob("candidatar-*.jsonl"))
    assert len(arquivos) == 1 and stat.S_IMODE(arquivos[0].stat().st_mode) == 0o600
    a = _assistencia(ambiente.banco)
    assert a["log_campos"] == str(arquivos[0])
    bruto = arquivos[0].read_text(encoding="utf-8")
    for s in _todos_sensiveis():
        assert s.lower() not in bruto.lower(), s
    assert json.loads(bruto.splitlines()[-1])["evento"] == "fim"

    # --sem-log: nenhum arquivo novo
    codigo, out, err = _rodar_candidatar(ambiente, servidor, pdf, capsys, "--sem-log", cfg=cfg)
    assert codigo == 0, out + err
    assert len(list(pasta_logs.glob("candidatar-*.jsonl"))) == 1
    assert _assistencia(ambiente.banco)["log_campos"] == ""


# --- 1d: "PRECISA DE LOGIN" uma vez por mudança de página ------------------------------------------------

def _cp_com_mensagens(pdf, **kw):
    msgs = []
    cp = Copiloto(RESPOSTAS_TESTE, pdf, alvos=ALVOS_TESTE, log=lambda *a, **k: msgs.append(" ".join(map(str, a))), **kw)
    return cp, msgs


def _avisos(msgs):
    return sum("PRECISA DE LOGIN" in m for m in msgs)


def test_aviso_de_login_uma_vez_por_mudanca_de_pagina(navegador, servidor, pdf):
    ctx = navegador.new_context()
    login, form = ctx.new_page(), ctx.new_page()
    notificacoes = []
    try:
        cp, msgs = _cp_com_mensagens(pdf, notificar=notificacoes.append)
        login.goto(f"{servidor}/gupy_login_fixture.html")
        form.goto(f"{servidor}/gupy_form_fixture.html")
        for _ in range(6):  # polling de duas abas: a aba do formulário não "reseta" o aviso da aba de login
            cp.passo(login)
            cp.passo(form)
        assert _avisos(msgs) == 1 and notificacoes == ["login"]
        # mesma tela com query diferente (redirecionamento de login): não é mudança de página
        login.goto(f"{servidor}/gupy_login_fixture.html?redirect=1&t=2")
        cp.passo(login)
        assert _avisos(msgs) == 1
        # saiu da tela de login e voltou: é mudança de página, avisa de novo (uma vez)
        login.goto(f"{servidor}/gupy_form_fixture.html")
        cp.passo(login)
        login.goto(f"{servidor}/gupy_login_fixture.html")
        cp.passo(login)
        cp.passo(login)
        assert _avisos(msgs) == 2
        assert cp.registro.avisos.count("tela de login detectada; aguardando o usuário") == 2
    finally:
        ctx.close()


def test_campos_pulados_nao_viram_falha(navegador, servidor, pdf):
    """Regressão: `_pular` não existia e todo campo pulado (pré-preenchido, login, opcional...) virava
    'falha ao tratar campo' em silêncio."""
    ctx = navegador.new_context()
    pg = ctx.new_page()
    try:
        cp, _ = _cp_com_mensagens(pdf)
        pg.goto(f"{servidor}/gupy_form_fixture.html")
        cp.passo(pg)
        cp.passo(pg)
        assert not [a for a in cp.registro.avisos if a.startswith("falha")], cp.registro.avisos
    finally:
        ctx.close()
