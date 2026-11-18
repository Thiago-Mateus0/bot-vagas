"""Botão PARAR, notificação ntfy (desligada por padrão, texto genérico) e geração do timer systemd (sem instalar).

Sem rede: o ntfy é sempre um `enviar` falso; as fontes usam ClienteHTTPFalso. O arquivo PARAR e o
notificacao.yaml apontam para pastas temporárias (ver conftest)."""

import json
import os
import subprocess
from pathlib import Path

import pytest
import yaml

from bot_vagas import cli, parada
from bot_vagas.banco import Banco
from bot_vagas.copiloto import Copiloto
from bot_vagas.fontes.greenhouse import Greenhouse
from bot_vagas.fontes.http import ClienteHTTPFalso, ErroHTTP
from bot_vagas.notificacao import MENSAGENS, ConfigNotificacao, Notificador, carregar_config
from bot_vagas.pipeline import avaliar, buscar, filtrar
from bot_vagas.respostas import valores_sensiveis

from test_candidatar import _argv, _cli, ambiente, pdf, servidor  # noqa: F401 (fixtures)
from test_preparar import RESPOSTAS_TESTE, ClienteFalhaSeChamado

FIX = Path(__file__).parent / "fixtures"
RAIZ = Path(__file__).resolve().parents[1]


def _gh_json():
    return json.loads((FIX / "greenhouse_exemplo.json").read_text(encoding="utf-8"))


# --- PARAR: módulo ------------------------------------------------------------------------------------

def test_parar_e_retomar_no_arquivo_isolado(_isolar_arquivos_locais):
    caminho = _isolar_arquivos_locais / "PARAR"
    assert parada.caminho_parar() == caminho  # os testes nunca tocam o PARAR do projeto
    assert not parada.parada_pedida()
    parada.verificar()  # sem arquivo: não levanta
    parada.pedir_parada("teste")
    assert caminho.exists() and parada.parada_pedida()
    assert "origem: teste" in caminho.read_text(encoding="utf-8")
    with pytest.raises(parada.ParadaPedida, match="PARAR"):
        parada.verificar()
    assert parada.retomar() is True and parada.retomar() is False
    assert not caminho.exists()


def test_cli_parar_retomar(capsys):
    assert cli.main(["parar"]) == 0
    assert parada.parada_pedida() and "PARAR acionado" in capsys.readouterr().out
    assert cli.main(["retomar"]) == 0
    assert not parada.parada_pedida() and "removido" in capsys.readouterr().out
    assert cli.main(["retomar"]) == 0 and "não havia" in capsys.readouterr().out


# --- PARAR: comandos não começam --------------------------------------------------------------------

@pytest.mark.parametrize("argv", [
    ["buscar"], ["filtrar"], ["avaliar"], ["avaliar", "--real"], ["rodar"], ["login-gupy"],
    ["preparar", "--arquivo", "x.txt"], ["candidatar", "https://x.gupy.io/job/1"],
])
def test_comandos_que_trabalham_recusam_com_parar(argv, tmp_path, capsys):
    parada.pedir_parada("teste")

    def nao_chamar(*a, **k):
        raise AssertionError("nada deveria ser criado com PARAR ativo")

    codigo = cli.main([*argv, *(["--banco", str(tmp_path / "v.db")] if argv[0] not in ("login-gupy",) else [])],
                      client_factory=nao_chamar, http_factory=nao_chamar, fetch=nao_chamar,
                      copiloto_cfg={"pasta_perfil": tmp_path / "nav", "headless": True})
    assert codigo == 4
    assert "PARADO" in capsys.readouterr().err
    assert not (tmp_path / "nav").exists() and not (tmp_path / "v.db").exists()


def test_comandos_de_leitura_funcionam_com_parar(tmp_path, capsys):
    parada.pedir_parada("teste")
    assert cli.main(["listar", "--banco", str(tmp_path / "v.db")]) == 0
    assert cli.main(["gerar-timer", "--saida", str(tmp_path / "sd")]) == 0


# --- PARAR: no meio de uma execução -------------------------------------------------------------------

class _FonteQuePara:
    """Fonte falsa que aciona o PARAR durante a própria busca (como o Thiago clicando no painel no meio)."""
    nome = "falsa"

    def buscar(self, http):
        parada.pedir_parada("teste")
        return []


def test_buscar_para_entre_fontes(tmp_path):
    gh = Greenhouse("exemploco")
    http = ClienteHTTPFalso({gh.url: _gh_json()})
    with Banco(tmp_path / "v.db") as banco:
        with pytest.raises(parada.ParadaPedida):
            buscar([_FonteQuePara(), gh], http, banco, log=lambda *a, **k: None)
    assert http.chamadas == []  # a segunda fonte nem foi consultada


def test_filtrar_e_avaliar_param_no_meio(tmp_path):
    from bot_vagas.filtro import carregar_filtros
    from bot_vagas.gasto import CUSTOS_PADRAO, Contador, carregar_custos

    gh = Greenhouse("exemploco")
    with Banco(tmp_path / "v.db") as banco:
        buscar([gh], ClienteHTTPFalso({gh.url: _gh_json()}), banco, log=lambda *a, **k: None)
        parada.pedir_parada("teste")
        with pytest.raises(parada.ParadaPedida):
            filtrar(banco, carregar_filtros(RAIZ / "filtros.yaml"), log=lambda *a, **k: None)
        parada.retomar()
        filtrar(banco, carregar_filtros(RAIZ / "filtros.yaml"), log=lambda *a, **k: None)
        parada.pedir_parada("teste")
        cliente = ClienteFalhaSeChamado()
        with pytest.raises(parada.ParadaPedida):
            avaliar(None, banco, carregar_filtros(RAIZ / "filtros.yaml"), cliente,
                    Contador(banco, carregar_custos(CUSTOS_PADRAO)), log=lambda *a, **k: None)
        assert banco.resumo_gasto()["chamadas"] == 0


def test_cli_rodar_para_no_meio_com_codigo_4(tmp_path, capsys, monkeypatch):
    arq = tmp_path / "fontes.yaml"
    arq.write_text("greenhouse: [exemploco]\n", encoding="utf-8")
    gh = Greenhouse("exemploco")

    class HttpQuePara(ClienteHTTPFalso):
        def get_json(self, url):
            r = super().get_json(url)
            parada.pedir_parada("painel")  # PARAR clicado durante a busca
            return r

    codigo = cli.main(["rodar", "--fontes", str(arq), "--banco", str(tmp_path / "v.db")],
                      http_factory=lambda: HttpQuePara({gh.url: _gh_json()}))
    assert codigo == 4 and "PARADO" in capsys.readouterr().err
    with Banco(tmp_path / "v.db") as b:  # buscou, mas não filtrou
        assert all(json.loads(r["detalhes"] or "{}").get("filtro") is None for r in b.listar())


def test_copiloto_para_no_meio_imprime_resumo_e_grava(ambiente, servidor, pdf, capsys, monkeypatch):
    original = Copiloto.passo

    def passo(self, pagina):
        r = original(self, pagina)
        parada.pedir_parada("painel")
        return r

    monkeypatch.setattr(Copiloto, "passo", passo)
    url = f"{servidor}/gupy_form_fixture.html"
    codigo = _cli(_argv(ambiente, "candidatar", url, "--pdf", str(pdf), "--empresa", "Acme", "--cargo", "Dev",
                        "--saida", str(ambiente.saida)),
                  copiloto_cfg=dict(ambiente.cfg, max_passos=None), client_factory=ClienteFalhaSeChamado)
    cap = capsys.readouterr()
    assert codigo == 4, cap.out + cap.err
    assert "RESUMO DA ASSISTÊNCIA" in cap.out and "parada pedida (arquivo PARAR)" in cap.out
    with Banco(ambiente.banco) as b:
        det = json.loads(b.listar("em_candidatura")[0]["detalhes"])
    assert det["assistencia"]["encerramento"] == "parada pedida (arquivo PARAR)"


def test_login_gupy_para_com_parar(ambiente, servidor, capsys):
    from bot_vagas.copiloto import login_gupy
    from test_candidatar import _cli_fn

    chamadas = {"n": 0}

    def parar():
        chamadas["n"] += 1
        return chamadas["n"] >= 2

    codigo = _cli_fn(lambda: login_gupy(ambiente.perfil_nav, url=f"{servidor}/gupy_login_fixture.html", headless=True,
                                        parar=parar, max_segundos=30))
    assert codigo == 4 and "PARADO" in capsys.readouterr().out


# --- PARAR: painel ---------------------------------------------------------------------------------

@pytest.fixture
def painel(tmp_path):
    from fastapi.testclient import TestClient

    from bot_vagas.gasto import CUSTOS_PADRAO
    from bot_vagas.painel import criar_app

    return TestClient(criar_app(tmp_path / "v.db", CUSTOS_PADRAO), base_url="http://127.0.0.1")


def test_painel_botao_parar_e_retomar(painel):
    html = painel.get("/").text
    assert 'action="/parar"' in html and "PARAR o bot" in html
    r = painel.post("/parar", headers={"Origin": "http://127.0.0.1:8765"}, follow_redirects=False)
    assert r.status_code == 303 and parada.parada_pedida()
    html = painel.get("/").text
    assert "PARADO" in html and 'action="/retomar"' in html
    r = painel.post("/retomar", headers={"Referer": "http://localhost:8765/vaga/x"}, follow_redirects=False)
    assert r.status_code == 303 and not parada.parada_pedida()


@pytest.mark.parametrize("cabecalhos", [{}, {"Origin": "https://site-malicioso.com"}, {"Origin": "null"},
                                        {"Referer": "https://evil.com/"}])
def test_painel_parar_recusa_post_de_outro_site(painel, cabecalhos):
    assert painel.post("/parar", headers=cabecalhos).status_code == 403
    assert not parada.parada_pedida()
    parada.pedir_parada("teste")
    assert painel.post("/retomar", headers=cabecalhos).status_code == 403
    assert parada.parada_pedida()


def test_painel_get_parar_nao_aciona(painel):
    assert painel.get("/parar").status_code == 405
    assert not parada.parada_pedida()


# --- notificação ntfy -------------------------------------------------------------------------------

class _EnviarFalso:
    def __init__(self, falhar=False):
        self.chamadas = []
        self.falhar = falhar

    def __call__(self, url, corpo, cabecalhos):
        if self.falhar:
            raise OSError("sem rede")
        self.chamadas.append((url, json.loads(corpo.decode("ascii")), cabecalhos))


def _config(tmp_path, conteudo):
    arq = tmp_path / "notificacao.yaml"
    arq.write_text(yaml.safe_dump(conteudo), encoding="utf-8")
    return arq


def test_notificacao_desligada_por_padrao(tmp_path):
    assert carregar_config(tmp_path / "nao-existe.yaml") == ConfigNotificacao()
    assert not carregar_config().ativo  # o padrão do conftest aponta para arquivo inexistente
    assert not carregar_config(_config(tmp_path, {"ntfy": {"ativo": False, "topico": "abc"}})).ativo
    assert not carregar_config(_config(tmp_path, {"ntfy": {"ativo": True}})).utilizavel  # sem tópico
    exemplo = yaml.safe_load((RAIZ / "notificacao.example.yaml").read_text(encoding="utf-8"))
    assert exemplo["ntfy"]["ativo"] is False
    enviar = _EnviarFalso()
    n = Notificador(enviar=enviar)
    assert n("login") is False and enviar.chamadas == []


@pytest.mark.parametrize("ntfy", [
    {"ativo": True, "topico": "ok", "servidor": "http://ntfy.sh"},  # sem TLS
    {"ativo": True, "topico": "ok", "servidor": "https://ntfy.sh/caminho"},
    {"ativo": True, "topico": "ok", "servidor": "https://user:senha@ntfy.sh"},
    {"ativo": True, "topico": "com espaço"}, {"ativo": True, "topico": "a/../b"}, {"ativo": True, "topico": "x" * 65},
])
def test_config_invalida_desliga_com_aviso(tmp_path, ntfy):
    avisos = []
    cfg = carregar_config(_config(tmp_path, {"ntfy": ntfy}), log=lambda *a, **k: avisos.append(a[0]))
    assert not cfg.ativo and len(avisos) == 1
    assert "senha" not in avisos[0]  # nem credencial nem tópico vão para o terminal


def test_envia_texto_generico_fixo_e_respeita_intervalo(tmp_path):
    cfg = carregar_config(_config(tmp_path, {"ntfy": {"ativo": True, "topico": "meu-topico_1"}}))
    assert cfg.ativo and cfg.servidor == "https://ntfy.sh"
    enviar, relogio = _EnviarFalso(), [1000.0]
    n = Notificador(cfg, enviar=enviar, relogio=lambda: relogio[0])
    assert n("login") is True
    assert n("login") is False  # repetido logo em seguida: não reenvia
    assert n("alarme_fonte") is True
    relogio[0] += 601
    assert n("login") is True
    assert [c[1]["title"] for c in enviar.chamadas] == [MENSAGENS["login"][0], MENSAGENS["alarme_fonte"][0],
                                                       MENSAGENS["login"][0]]
    url, corpo, cab = enviar.chamadas[0]
    assert url == "https://ntfy.sh/" and cab == {"Content-Type": "application/json"}
    assert corpo == {"topic": "meu-topico_1", "title": MENSAGENS["login"][0], "message": MENSAGENS["login"][1],
                     "tags": ["robot"]}
    with pytest.raises(ValueError):
        n("texto livre com dados")  # só eventos da tabela fixa


def test_mensagens_nao_tem_dados_pessoais():
    perfil = RAIZ / "perfil.yaml"
    proibidos = valores_sensiveis(RESPOSTAS_TESTE)
    for arq in (RAIZ / "respostas.yaml", perfil):
        if arq.exists():
            proibidos += valores_sensiveis(yaml.safe_load(arq.read_text(encoding="utf-8")))
    texto = json.dumps(MENSAGENS, ensure_ascii=False).lower()
    for s in proibidos:
        if len(s) >= 5:
            assert s.lower() not in texto, s
    assert "@" not in texto and "http" not in texto


def test_falha_de_rede_nao_derruba(tmp_path):
    cfg = carregar_config(_config(tmp_path, {"ntfy": {"ativo": True, "topico": "topico-secreto-xyz"}}))
    avisos = []
    n = Notificador(cfg, enviar=_EnviarFalso(falhar=True), log=lambda *a, **k: avisos.append(a[0]))
    assert n("login") is False
    assert len(avisos) == 1 and "OSError" in avisos[0] and "topico-secreto-xyz" not in avisos[0]


def test_copiloto_notifica_login_uma_vez_pela_cli(ambiente, servidor, pdf, capsys):
    enviar = _EnviarFalso()
    n = Notificador(ConfigNotificacao(ativo=True, topico="t"), enviar=enviar)
    url = f"{servidor}/gupy_login_fixture.html"
    codigo = _cli(_argv(ambiente, "candidatar", url, "--pdf", str(pdf), "--empresa", "Acme Secreta",
                        "--cargo", "Cargo Secreto", "--saida", str(ambiente.saida)),
                  copiloto_cfg=dict(ambiente.cfg, max_passos=5), client_factory=ClienteFalhaSeChamado, notificador=n)
    assert codigo == 0, capsys.readouterr()
    assert n.enviados == ["login"] and len(enviar.chamadas) == 1
    corpo = json.dumps(enviar.chamadas[0][1], ensure_ascii=False)
    for s in valores_sensiveis(RESPOSTAS_TESTE) + ["Acme", "Secret", "127.0.0.1", "gupy_login"]:
        assert s not in corpo, s


def test_buscar_com_alarme_notifica(tmp_path):
    from test_gupy import URL1

    arq = tmp_path / "fontes.yaml"
    arq.write_text("gupy:\n  termos: [estagio ti]\n  pausa_segundos: 0\n", encoding="utf-8")
    enviar = _EnviarFalso()
    n = Notificador(ConfigNotificacao(ativo=True, topico="t"), enviar=enviar)
    codigo = cli.main(["buscar", "--fontes", str(arq), "--banco", str(tmp_path / "v.db")],
                      http_factory=lambda: ClienteHTTPFalso({URL1: "<html>sem next data</html>"}), notificador=n)
    assert codigo == 1 and n.enviados == ["alarme_fonte"]


def test_testar_notificacao(capsys):
    assert cli.main(["testar-notificacao"]) == 1 and "desligadas" in capsys.readouterr().err
    enviar = _EnviarFalso()
    n = Notificador(ConfigNotificacao(ativo=True, topico="t"), enviar=enviar)
    assert cli.main(["testar-notificacao"], notificador=n) == 0 and n.enviados == ["teste"]


# --- rodar + timer systemd ----------------------------------------------------------------------------

def test_rodar_busca_e_filtra_sem_api(tmp_path, capsys):
    arq = tmp_path / "fontes.yaml"
    arq.write_text("greenhouse: [exemploco]\n", encoding="utf-8")
    gh = Greenhouse("exemploco")

    def nao_chamar():
        raise AssertionError("rodar nunca cria cliente da API")

    codigo = cli.main(["rodar", "--fontes", str(arq), "--banco", str(tmp_path / "v.db")],
                      http_factory=lambda: ClienteHTTPFalso({gh.url: _gh_json()}), client_factory=nao_chamar)
    out = capsys.readouterr().out
    assert codigo == 0, out
    assert "nenhuma candidatura é enviada" in out and "fim da busca" in out
    with Banco(tmp_path / "v.db") as b:
        assert b.resumo_gasto()["chamadas"] == 0
        assert all(json.loads(r["detalhes"] or "{}").get("filtro") in ("passou", "bloqueada") for r in b.listar())


def test_gerar_timer_escreve_arquivos_sem_instalar(tmp_path, capsys, monkeypatch):
    chamadas = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: chamadas.append(a))
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: chamadas.append(a))
    destino = tmp_path / "sd"
    assert cli.main(["gerar-timer", "--saida", str(destino)]) == 0
    out = capsys.readouterr().out
    assert chamadas == []  # nenhum systemctl
    assert sorted(p.name for p in destino.iterdir()) == ["bot-vagas.service", "bot-vagas.timer"]
    timer = (destino / "bot-vagas.timer").read_text(encoding="utf-8")
    service = (destino / "bot-vagas.service").read_text(encoding="utf-8")
    assert "OnCalendar=*-*-* 14:00:00" in timer and "Persistent=true" in timer
    assert "Unit=bot-vagas.service" in timer and "WantedBy=timers.target" in timer
    assert "Type=oneshot" in service and "-m bot_vagas.cli rodar" in service
    assert "--real" not in service and "candidatar" not in service and "TimeoutStartSec=4h" in service
    assert "NADA foi instalado" in out and "systemctl --user enable --now bot-vagas.timer" in out
    home = str(Path.home())
    if str(RAIZ).startswith(home + os.sep):
        assert "%h/" in service and home not in service  # portátil e sem o nome de usuário


def test_gerar_timer_horario_invalido(tmp_path):
    from bot_vagas.agendamento import conteudo_timer

    assert "OnCalendar=*-*-* 09:05:00" in conteudo_timer("9:05")
    for ruim in ("25:00", "14", "aa:bb", "14:60"):
        with pytest.raises(ValueError):
            conteudo_timer(ruim)


@pytest.mark.skipif(RAIZ.resolve() != (Path.home() / "bot-vagas").resolve(),
                    reason="os arquivos de systemd/ do repositório assumem o clone em ~/bot-vagas")
def test_arquivos_systemd_do_repo_batem_com_o_gerador():
    from bot_vagas.agendamento import conteudo_service, conteudo_timer

    assert (RAIZ / "systemd" / "bot-vagas.timer").read_text(encoding="utf-8") == conteudo_timer()
    assert (RAIZ / "systemd" / "bot-vagas.service").read_text(encoding="utf-8") == conteudo_service()
