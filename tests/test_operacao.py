"""Botão PARAR, notificação ntfy, rodada diária (`rodar`) e geração do timer do systemd. Sem rede, sem API."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bot_vagas import cli
from bot_vagas.agendamento import gerar_unidades, validar_hora
from bot_vagas.banco import Banco
from bot_vagas.fontes.http import ClienteHTTPFalso
from bot_vagas.gasto import CUSTOS_PADRAO
from bot_vagas.notificacao import (
    MENSAGENS, ConfigNotificacao, ConfigNotificacaoInvalida, Notificador, carregar_notificacao, montar_mensagem,
)
from bot_vagas.painel import criar_app, origem_permitida
from bot_vagas.parada import ParadaSolicitada, caminho_parar, parar_solicitado, retomar, solicitar_parada
from bot_vagas.pipeline import buscar

FIX = Path(__file__).parent / "fixtures"


# --- PARAR -----------------------------------------------------------------------------

def test_parar_e_retomar_pela_cli(capsys):
    assert not parar_solicitado()
    assert cli.main(["parar", "vou", "sair"]) == 0
    assert parar_solicitado() and "vou sair" in caminho_parar().read_text(encoding="utf-8")
    assert "PARAR ligado" in capsys.readouterr().out
    assert cli.main(["retomar"]) == 0
    assert not parar_solicitado()
    assert cli.main(["retomar"]) == 0 and "não havia" in capsys.readouterr().out


@pytest.mark.parametrize("argv", [["buscar"], ["filtrar"], ["avaliar"], ["rodar"], ["login-gupy"],
                                  ["candidatar", "https://x.gupy.io/job/1"], ["preparar", "https://x.gupy.io/job/1"]])
def test_comandos_de_trabalho_nao_comecam_com_parar(argv, tmp_path, capsys):
    solicitar_parada()
    chamou = []
    codigo = cli.main([*argv, *(["--banco", str(tmp_path / "t.db")] if argv[0] != "login-gupy" else [])],
                      http_factory=lambda: chamou.append(1), fetch=lambda u: chamou.append(1),
                      client_factory=lambda: chamou.append(1))
    assert codigo == 4
    assert "PARADO" in capsys.readouterr().err
    assert chamou == [] and not (tmp_path / "t.db").exists()


def test_comandos_de_consulta_funcionam_com_parar(tmp_path, capsys):
    solicitar_parada()
    assert cli.main(["listar", "--banco", str(tmp_path / "t.db")]) == 0
    assert cli.main(["marcar", "x", "enviada", "--banco", str(tmp_path / "t.db")]) == 1  # roda (vaga não existe)


class _FonteQueAcionaParar:
    nome = "teste"
    chamadas = 0

    def buscar(self, http):
        type(self).chamadas += 1
        solicitar_parada("durante a busca")
        return []


def test_parar_no_meio_da_busca_interrompe_entre_fontes(tmp_path):
    _FonteQueAcionaParar.chamadas = 0
    with Banco(tmp_path / "t.db") as banco, pytest.raises(ParadaSolicitada):
        buscar([_FonteQueAcionaParar(), _FonteQueAcionaParar()], ClienteHTTPFalso({}), banco, log=lambda *a, **k: None)
    assert _FonteQueAcionaParar.chamadas == 1


def test_parar_no_meio_do_avaliar_nao_gasta(tmp_path):
    """Com PARAR ligado no meio, o avaliar sai antes de chamar o cliente (nenhum gasto)."""
    from bot_vagas.filtro import carregar_filtros
    from bot_vagas.gasto import Contador, carregar_custos
    from bot_vagas.perfil import PERFIL_PADRAO, carregar_perfil
    from bot_vagas.pipeline import avaliar, filtrar

    from bot_vagas.fontes.greenhouse import Greenhouse
    gh = Greenhouse("exemploco")
    http = ClienteHTTPFalso({gh.url: json.loads((FIX / "greenhouse_exemplo.json").read_text(encoding="utf-8"))})

    class _Cliente:
        messages = property(lambda self: (_ for _ in ()).throw(AssertionError("não devia chamar")))

    with Banco(tmp_path / "t.db") as banco:
        buscar([gh], http, banco, log=lambda *a, **k: None)
        filtrar(banco, carregar_filtros(), log=lambda *a, **k: None)
        solicitar_parada()
        with pytest.raises(ParadaSolicitada):
            avaliar(carregar_perfil(PERFIL_PADRAO), banco, carregar_filtros(), _Cliente(),
                    Contador(banco, carregar_custos(CUSTOS_PADRAO)), log=lambda *a, **k: None)
        assert banco.resumo_gasto()["chamadas"] == 0


def test_painel_botao_parar_e_retomar(tmp_path):
    c = TestClient(criar_app(tmp_path / "t.db", CUSTOS_PADRAO), base_url="http://127.0.0.1")
    html = c.get("/").text
    assert 'action="/parar"' in html and "Parar tudo" in html
    # sem Origin (ex.: outro programa) ou com Origin de outro site (CSRF): recusado
    assert c.post("/parar", follow_redirects=False).status_code == 403
    assert c.post("/parar", headers={"Origin": "https://site-malicioso.example"}, follow_redirects=False).status_code == 403
    assert not parar_solicitado()
    r = c.post("/parar", headers={"Origin": "http://127.0.0.1:8765"}, follow_redirects=False)
    assert r.status_code == 303 and parar_solicitado()
    html = c.get("/").text
    assert "PARADO" in html and 'action="/retomar"' in html
    assert c.post("/retomar", headers={"Origin": "http://localhost:8765"}, follow_redirects=False).status_code == 303
    assert not parar_solicitado()
    assert c.get("/parar").status_code == 405  # GET não aciona nada


def test_origem_permitida():
    assert origem_permitida("http://127.0.0.1:8765") and origem_permitida("http://localhost")
    assert origem_permitida("http://[::1]:8765")
    for o in (None, "", "null", "https://evil.com", "http://127.0.0.1.evil.com", "http://127.0.0.1:8765/x"):
        assert not origem_permitida(o), o


# --- notificação -----------------------------------------------------------------------

def test_notificacao_desligada_por_padrao(tmp_path):
    assert not carregar_notificacao(tmp_path / "nao-existe.yaml").ligada
    (tmp_path / "n.yaml").write_text("ntfy:\n  topico: null\n", encoding="utf-8")
    assert not carregar_notificacao(tmp_path / "n.yaml").ligada
    enviados = []
    assert Notificador(enviar=lambda *a: enviados.append(a)).notificar("login") is False
    assert enviados == []


def test_notificacao_config_valida_e_invalida(tmp_path):
    arq = tmp_path / "n.yaml"
    arq.write_text("ntfy:\n  topico: meu-topico_secreto123\n", encoding="utf-8")
    c = carregar_notificacao(arq)
    assert c.ligada and c.servidor == "https://ntfy.sh"
    for ruim in ("ntfy:\n  topico: 'a b'\n", "ntfy:\n  topico: abc\n", "ntfy:\n  topico: topico-ok-123\n  servidor: http://ntfy.sh\n",
                 "ntfy:\n  topico: topico-ok-123\n  servidor: https://ntfy.sh/outro\n"):
        arq.write_text(ruim, encoding="utf-8")
        with pytest.raises(ConfigNotificacaoInvalida):
            carregar_notificacao(arq)


def test_mensagens_sao_fixas_e_genericas():
    for evento, (titulo, msg) in MENSAGENS.items():
        texto = (titulo + msg).lower()
        for proibido in ("http", "@", "gupy.io", "r$", "{", "}") if "{n}" not in msg else ("http", "@", "gupy.io", "r$"):
            assert proibido not in texto, (evento, proibido)
    assert montar_mensagem("rodada", n="3")[1].startswith("Rodada diária concluída: 3 ")
    with pytest.raises(ValueError):
        montar_mensagem("rodada", n="Maceió")  # só inteiro entra
    with pytest.raises(KeyError):
        montar_mensagem("texto livre qualquer")


def test_notificador_uma_vez_por_evento_e_falha_de_rede_nao_derruba():
    enviados, avisos = [], []
    n = Notificador(ConfigNotificacao(topico="topico-teste-123"), enviar=lambda *a: enviados.append(a),
                    log=lambda *a, **k: avisos.append(a))
    assert n.notificar("login") and not n.notificar("login")
    assert n.notificar("login", repetir=True)
    assert len(enviados) == 2
    url, corpo, cab = enviados[0]
    assert url == "https://ntfy.sh/topico-teste-123" and cab["Title"].isascii()

    def quebrar(*a):
        raise OSError("sem rede")

    falha = Notificador(ConfigNotificacao(topico="topico-teste-123"), enviar=quebrar, log=lambda *a, **k: avisos.append(a))
    assert falha.notificar("erro") is False and avisos


def test_testar_notificacao_cli(tmp_path, capsys, monkeypatch):
    assert cli.main(["testar-notificacao", "--notificacao", str(tmp_path / "x.yaml")]) == 1
    assert "DESLIGADA" in capsys.readouterr().out
    arq = tmp_path / "n.yaml"
    arq.write_text("ntfy:\n  topico: topico-teste-123\n", encoding="utf-8")
    enviados = []
    monkeypatch.setattr("bot_vagas.notificacao._enviar_http", lambda *a: enviados.append(a))
    assert cli.main(["testar-notificacao", "--notificacao", str(arq)]) == 0
    assert len(enviados) == 1 and enviados[0][1].decode() == MENSAGENS["teste"][1]


# --- rodar (rodada diária) ----------------------------------------------------------------

@pytest.fixture
def rodada(tmp_path, monkeypatch):
    from bot_vagas.fontes.greenhouse import Greenhouse
    from bot_vagas.fontes.lever import Lever

    fontes = tmp_path / "fontes.yaml"
    fontes.write_text("greenhouse: [exemploco]\nlever: [exemplolever]\n", encoding="utf-8")
    respostas = {Greenhouse("exemploco").url: json.loads((FIX / "greenhouse_exemplo.json").read_text(encoding="utf-8")),
                 Lever("exemplolever").url: json.loads((FIX / "lever_exemplo.json").read_text(encoding="utf-8"))}
    notif = tmp_path / "n.yaml"
    notif.write_text("ntfy:\n  topico: topico-teste-123\n", encoding="utf-8")
    enviados = []
    monkeypatch.setattr("bot_vagas.notificacao._enviar_http", lambda url, corpo, cab: enviados.append(corpo.decode()))
    argv = ["rodar", "--banco", str(tmp_path / "t.db"), "--fontes", str(fontes), "--notificacao", str(notif)]
    return argv, (lambda: ClienteHTTPFalso(respostas)), enviados, tmp_path


def test_rodar_busca_filtra_e_notifica(rodada, capsys):
    argv, http, enviados, tmp = rodada
    assert cli.main(argv, http_factory=http) == 0
    out = capsys.readouterr().out
    assert "rodada concluída: 6 baixadas, 6 novas, 2 passaram no filtro" in out
    assert enviados == ["Rodada diária concluída: 2 vaga(s) nova(s) passaram no filtro."]
    with Banco(tmp / "t.db") as b:
        assert b.contar_por_status() == {"nova": 2, "bloqueada": 4}
        assert b.resumo_gasto()["chamadas"] == 0  # nunca chama a API paga


def test_rodar_com_erro_notifica_erro(rodada, capsys):
    argv, _, enviados, _ = rodada

    def quebra():
        raise RuntimeError("falha qualquer")

    with pytest.raises(RuntimeError):
        cli.main(argv, http_factory=quebra)
    assert enviados == [MENSAGENS["erro"][1]]


def test_rodar_parado_no_meio_notifica_e_sai_4(rodada, capsys, monkeypatch):
    argv, http, enviados, _ = rodada
    import bot_vagas.pipeline as pl

    original = pl.exigir_nao_parado
    chamadas = []

    def parar_na_segunda():
        chamadas.append(1)
        if len(chamadas) == 2:
            solicitar_parada()
        original()

    monkeypatch.setattr(pl, "exigir_nao_parado", parar_na_segunda)
    assert cli.main(argv, http_factory=http) == 4
    assert "PARADO" in capsys.readouterr().err
    assert enviados == [MENSAGENS["parado"][1]]


# --- timer do systemd ----------------------------------------------------------------------

def test_gerar_unidades_timer_14h_persistent():
    u = gerar_unidades(raiz="/home/fulano/meu projeto/bot-vagas", python="/home/fulano/meu projeto/bot-vagas/.venv/bin/python")
    timer, service = u["bot-vagas.timer"], u["bot-vagas.service"]
    assert "OnCalendar=*-*-* 14:00:00" in timer and "Persistent=true" in timer and "WantedBy=timers.target" in timer
    assert 'ExecStart="/home/fulano/meu projeto/bot-vagas/.venv/bin/python" -m bot_vagas.cli rodar' in service
    assert "WorkingDirectory=/home/fulano/meu projeto/bot-vagas" in service and "Type=oneshot" in service
    assert "--real" not in service and "ANTHROPIC" not in service  # nada pago, nenhuma chave


def test_hora_do_timer():
    assert validar_hora("9:05") == "09:05"
    for ruim in ("24:00", "14h", "14:60", "abc"):
        with pytest.raises(ValueError):
            validar_hora(ruim)


def test_gerar_timer_cli_so_escreve_arquivos(tmp_path, capsys, monkeypatch):
    import subprocess

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(AssertionError("não instala nada")))
    destino = tmp_path / "systemd"
    assert cli.main(["gerar-timer", "--saida", str(destino), "--hora", "14:00"]) == 0
    assert sorted(p.name for p in destino.iterdir()) == ["bot-vagas.service", "bot-vagas.timer"]
    out = capsys.readouterr().out
    assert "NADA foi instalado" in out and "systemctl --user enable --now bot-vagas.timer" in out
    assert cli.main(["gerar-timer", "--saida", str(destino), "--hora", "25:00"]) == 2


def test_rodar_sem_nenhuma_fonte_respondendo_e_erro(rodada, capsys):
    argv, _, enviados, _ = rodada
    assert cli.main(argv, http_factory=lambda: ClienteHTTPFalso({})) == 1  # todas as URLs dão erro
    assert "nenhuma fonte respondeu" in capsys.readouterr().err
    assert enviados == [MENSAGENS["erro"][1]]
