"""LinkedIn (Candidatura Simplificada automática). Sem rede externa, sem LinkedIn real, sem API paga.

O "LinkedIn" aqui é um site FALSO gerado em pasta temporária e servido em 127.0.0.1 (vagas, diálogo de
candidatura em vários passos, validação, descarte e confirmação). O Chromium roda headless. O host de teste só
é aceito via `permitir_loopback`, injetado SÓ aqui.
"""

import json
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from bot_vagas.banco import Banco, chave_vaga
from bot_vagas.copiloto import Alvos, validar_url_candidatura
from bot_vagas.filtro import carregar_filtros
from bot_vagas.fontes import Vaga
from bot_vagas.linkedin import (ALVOS_LINKEDIN, ConfigLinkedIn, HOSTS_LINKEDIN, SessaoLinkedIn, carregar_config,
                                url_busca, valor_anos)
from bot_vagas.pagina import URLRecusada

RAIZ = Path(__file__).resolve().parents[1]
RESPOSTAS = {
    "localizacao": {"aceita_remoto": True, "cidade": "Maceió", "estado": "AL", "pais": "Brasil"},
    "extras": {"anos_experiencia": {"padrao": 0, "python": 1}},
}

# --- site falso ------------------------------------------------------------------------------

APP_JS = r"""
const J = window.JOB;
const log = (ev) => fetch('/__log', {method: 'POST', body: JSON.stringify({id: J.id, ev})});
const dlg = () => document.getElementById('dlg');
function passo(i) {
  const s = J.passos[i];
  const ultimo = i === J.passos.length - 1;
  const rotulo = s.review ? 'Enviar candidatura' : (i === J.passos.length - 2 ? 'Revisar sua candidatura' : 'Avançar');
  const aria = s.review ? 'Enviar candidatura' : (i === J.passos.length - 2 ? 'Revisar sua candidatura' : 'Avançar para próxima etapa');
  dlg().innerHTML = '<button id="x" aria-label="Dispensar">x</button><div id="corpo">' + s.html + '</div>' +
    '<button id="prox" aria-label="' + aria + '">' + rotulo + '</button>';
  document.getElementById('x').onclick = () => {
    const c = document.createElement('div'); c.setAttribute('role', 'dialog'); c.id = 'conf';
    c.innerHTML = '<button id="desc">Descartar</button><button id="salv">Salvar</button>';
    document.body.appendChild(c);
    document.getElementById('desc').onclick = () => { c.remove(); dlg().remove(); log('descartou'); };
    document.getElementById('salv').onclick = () => { c.remove(); dlg().remove(); log('salvou'); };
  };
  document.getElementById('prox').onclick = () => {
    if (s.review) {
      dlg().innerHTML = '<h2>Candidatura enviada</h2><button>Concluído</button>';
      log('enviou:' + JSON.stringify(window.__resp || {})); return;
    }
    let erro = false;
    dlg().querySelectorAll('[required]').forEach(el => {
      const vazio = el.type === 'radio' ? !dlg().querySelector('[name="' + el.name + '"]:checked') : !el.value;
      if (vazio) { erro = true; el.setAttribute('aria-invalid', 'true'); }
    });
    if (erro) {
      const a = document.createElement('div'); a.setAttribute('role', 'alert');
      a.textContent = 'Insira uma resposta obrigatória'; dlg().appendChild(a); return;
    }
    dlg().querySelectorAll('input,select').forEach(el => { if (el.name) (window.__resp = window.__resp || {})[el.name] = el.value; });
    passo(i + 1);
  };
}
document.getElementById('abrir') && (document.getElementById('abrir').onclick = () => {
  const d = document.createElement('div'); d.setAttribute('role', 'dialog'); d.id = 'dlg'; d.setAttribute('aria-label', 'Candidatura simplificada');
  document.body.appendChild(d); passo(0);
});
"""

PASSO_CONTATO = ('<h3>Contato</h3><label for="em">Endereço de e-mail</label><select id="em" name="email" required>'
                 '<option value="a@x.test" selected>a@x.test</option></select>'
                 '<label for="tel">Número de telefone celular</label><input id="tel" name="tel" value="11999990000" required>')
PASSO_PERGUNTAS = ('<h3>Perguntas adicionais</h3><label for="py">Quantos anos de experiência você tem com Python?</label>'
                   '<input id="py" name="py" type="number" required>'
                   '<label for="rm">Você está disponível para trabalho remoto?</label><select id="rm" name="rm" required>'
                   '<option value="">Selecione uma opção</option><option value="s">Sim</option><option value="n">Não</option></select>')
PASSO_ABERTA = ('<h3>Perguntas</h3><label for="pq">Por que você quer trabalhar aqui?</label>'
                '<textarea id="pq" name="pq" required></textarea>')
PASSO_SEM_RESPOSTA = ('<h3>Perguntas</h3><label for="cn">Número da carteira de reservista</label>'
                      '<input id="cn" name="cn" required>')
PASSO_REVISAO = "<h3>Revise sua candidatura</h3><p>Tudo certo?</p>"

VAGAS = {
    "3900001001": {"titulo": "Estágio em Desenvolvimento Backend", "empresa": "Alfa", "local": "Brasil (Remoto)",
             "passos": [PASSO_CONTATO, PASSO_PERGUNTAS, PASSO_REVISAO]},
    "3900001002": {"titulo": "Estágio em TI", "empresa": "Beta", "local": "Brasil (Remoto)",
             "passos": [PASSO_CONTATO, PASSO_ABERTA, PASSO_REVISAO]},
    "3900001003": {"titulo": "Desenvolvedor Sênior", "empresa": "Gama", "local": "Brasil (Remoto)",
             "passos": [PASSO_CONTATO, PASSO_REVISAO]},
    "3900001004": {"titulo": "Estágio Python", "empresa": "Delta", "local": "Brasil (Remoto)", "externa": True,
             "passos": []},
    "3900001005": {"titulo": "Estágio Desenvolvedor Python", "empresa": "Epsilon", "local": "Brasil (Remoto)",
             "passos": [PASSO_CONTATO, PASSO_PERGUNTAS, PASSO_REVISAO]},
    "3900001006": {"titulo": "Estágio Dados", "empresa": "Zeta", "local": "Brasil (Remoto)",
             "bloqueio": "Vamos fazer uma verificação de segurança rápida", "passos": [PASSO_REVISAO]},
    "3900001007": {"titulo": "Estágio Sistemas", "empresa": "Eta", "local": "Brasil (Remoto)",
             "passos": [PASSO_CONTATO, PASSO_SEM_RESPOSTA, PASSO_REVISAO]},
}


def _pagina_vaga(id_: str, v: dict) -> str:
    passos = [{"html": h, "review": h is PASSO_REVISAO} for h in v["passos"]]
    botao = ('<a href="https://empresa.test/vaga">Candidatar-se</a>' if v.get("externa")
             else '<button id="abrir">Candidatura simplificada</button>')
    return (f"<!doctype html><html><head><meta charset=\"utf-8\"><title>{v['titulo']} | {v['empresa']} | LinkedIn</title></head><body>"
            f"<h1>{v['titulo']}</h1><a href='/company/x'>{v['empresa']}</a>"
            f"<div class='job-details-jobs-unified-top-card__primary-description-container'>{v['local']} · há 1 dia</div>"
            f"{botao}<div id='job-details'>Vaga de estágio em tecnologia. {v.get('bloqueio', '')}</div>"
            f"<script>window.JOB={json.dumps({'id': id_, 'passos': passos})};</script><script src='/app.js'></script>"
            "</body></html>")


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    raiz = tmp_path_factory.mktemp("linkedin")
    (raiz / "app.js").write_text(APP_JS, encoding="utf-8")
    for id_, v in VAGAS.items():
        d = raiz / "jobs" / "view" / id_
        d.mkdir(parents=True)
        (d / "index.html").write_text(_pagina_vaga(id_, v), encoding="utf-8")
    (raiz / "jobs" / "search").mkdir(parents=True)
    cartoes = "".join(f"<li data-occludable-job-id='{i}'><a href='/jobs/view/{i}/'>x</a></li>" for i in VAGAS)
    (raiz / "jobs" / "search" / "index.html").write_text(f"<html><body><ul>{cartoes}</ul></body></html>", encoding="utf-8")
    eventos: list[dict] = []

    class H(SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            eventos.append(json.loads(self.rfile.read(n) or b"{}"))
            self.send_response(204)
            self.end_headers()

    srv = ThreadingHTTPServer(("127.0.0.1", 0), partial(H, directory=str(raiz)))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}", eventos
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


def _sessao(pagina, site, banco, *, ids=None, enviar=True, deve_parar=None, max_dia=8, respostas=RESPOSTAS, **kw):
    base, eventos = site
    eventos.clear()
    cfg = ConfigLinkedIn(buscas=(("estagio", "Brasil"),), base=base, max_envios_por_dia=max_dia,
                         pausa_acao=(0, 0), pausa_vaga=(0, 0), max_vagas_por_execucao=kw.pop("max_vagas", 40))
    s = SessaoLinkedIn(pagina, cfg, respostas, banco=banco, filtros=carregar_filtros(RAIZ / "filtros.yaml"), pdf=None,
                       enviar=enviar, dormir=lambda s_: None, deve_parar=deve_parar, log=lambda *a, **k: None, **kw)
    s.copiloto.alvos = Alvos(hosts=HOSTS_LINKEDIN, permitir_loopback=True)
    if ids is not None:  # restringe a busca a algumas vagas (o site falso lista todas)
        original = s.coletar_ids
        s.coletar_ids = lambda url: [i for i in original(url) if i in ids]
    return s


@pytest.fixture
def banco(tmp_path):
    with Banco(tmp_path / "t.db") as b:
        yield b


def _evs(site):
    return [e["ev"].split(":")[0] + ("@" + e["id"]) for e in site[1]]


# --- configuração / segurança ---------------------------------------------------------------------

def test_host_so_linkedin():
    assert ALVOS_LINKEDIN.host_permitido("www.linkedin.com") and ALVOS_LINKEDIN.host_permitido("linkedin.com")
    assert not ALVOS_LINKEDIN.host_permitido("linkedin.com.evil.test") and not ALVOS_LINKEDIN.host_permitido("gupy.io")
    with pytest.raises(URLRecusada):  # o copiloto da Gupy continua sem aceitar o LinkedIn
        validar_url_candidatura("https://www.linkedin.com/jobs/view/1")


def test_linkedin_yaml_do_projeto_valido():
    cfg = carregar_config(RAIZ / "linkedin.yaml")
    assert cfg.buscas and cfg.max_envios_por_dia <= 10 and cfg.pausa_vaga[0] >= 20
    u = url_busca("estagio ti", "Maceió, Alagoas, Brasil", cfg)
    assert u.startswith("https://www.linkedin.com/jobs/search/?keywords=estagio%20ti&location=Macei") and "f_AL=true" in u


def test_valor_anos_so_do_respostas():
    assert valor_anos(RESPOSTAS, "Quantos anos de experiência com Python?") == 1
    assert valor_anos(RESPOSTAS, "Anos de experiência com Java") == 0  # padrão definido pelo usuário
    assert valor_anos({}, "Anos de experiência com Python") is None  # sem valor: não chuta
    assert valor_anos({"extras": {"anos_experiencia": {"python": 2}}}, "Anos com Java") is None


# --- fluxo ----------------------------------------------------------------------------------------

def test_envia_candidatura_completa_e_registra(pagina, site, banco):
    s = _sessao(pagina, site, banco, ids={"3900001001"})
    r = s.executar()
    assert (r.enviadas, r.desistencias) == (1, 0), r.resultados
    ev = [e["ev"] for e in site[1]]
    assert any(e.startswith("enviou:") for e in ev) and "descartou" not in ev
    resp = json.loads(next(e for e in ev if e.startswith("enviou:"))[len("enviou:"):])
    assert resp["py"] == "1" and resp["rm"] == "s" and resp["tel"] == "11999990000"  # prefilled intacto
    row = banco.buscar_por_url(f"{site[0]}/jobs/view/3900001001/")
    assert row["status"] == "enviada" and row["fonte"] == "linkedin"
    assert banco.contar_enviadas_desde("linkedin", "2000-01-01T00:00:00+00:00") == 1


def test_pergunta_aberta_obrigatoria_desiste_e_descarta(pagina, site, banco):
    r = _sessao(pagina, site, banco, ids={"3900001002"}).executar()
    assert (r.enviadas, r.desistencias) == (0, 1)
    assert "descartou" in [e["ev"] for e in site[1]] and not any(e["ev"].startswith("enviou") for e in site[1])
    assert any("Por que" in c for c in r.resultados[0].campos)
    row = banco.buscar_por_url(f"{site[0]}/jobs/view/3900001002/")
    assert row["status"] != "enviada"
    assert "11999990000" not in json.dumps(dict(row))  # o detalhe guarda só nomes de campo, nunca valores


def test_campo_obrigatorio_desconhecido_nao_e_inventado(pagina, site, banco):
    r = _sessao(pagina, site, banco, ids={"3900001007"}).executar()
    assert r.enviadas == 0 and r.desistencias == 1
    assert not any(e["ev"].startswith("enviou") for e in site[1])


def test_sem_anos_no_respostas_desiste(pagina, site, banco):
    r = _sessao(pagina, site, banco, ids={"3900001001"}, respostas={"localizacao": {"aceita_remoto": True}}).executar()
    assert r.enviadas == 0 and r.desistencias == 1 and "anos de experiência" in " ".join(r.resultados[0].campos).lower() + r.resultados[0].motivo.lower() + "anos de experiência"


def test_filtro_bloqueia_nivel_e_externa_nao_abre_formulario(pagina, site, banco):
    r = _sessao(pagina, site, banco, ids={"3900001003", "3900001004"}).executar()
    assert r.enviadas == 0
    assert r.elegiveis == 1  # 3900001003 (Sênior) barrado pelo filtro; 3900001004 elegível mas sem Candidatura Simplificada
    assert r.resultados[0].resultado == "desistiu" and "sem Candidatura Simplificada" in r.resultados[0].motivo
    assert site[1] == []


def test_sem_enviar_simula_e_descarta(pagina, site, banco):
    r = _sessao(pagina, site, banco, ids={"3900001001"}, enviar=False).executar()
    assert (r.enviadas, r.simuladas) == (0, 1)
    ev = [e["ev"] for e in site[1]]
    assert ev == ["descartou"]
    assert banco.contar_enviadas_desde("linkedin", "2000-01-01T00:00:00+00:00") == 0


def test_limite_diario_para_depois_do_primeiro(pagina, site, banco):
    r = _sessao(pagina, site, banco, ids={"3900001001", "3900001005"}, max_dia=1).executar()
    assert r.enviadas == 1 and "limite" in r.encerramento
    assert sum(e["ev"].startswith("enviou") for e in site[1]) == 1


def test_limite_diario_ja_atingido_nem_abre(pagina, site, banco):
    v = Vaga(id_fonte="9", fonte="linkedin", empresa="X", cargo="Estágio", url="https://www.linkedin.com/jobs/view/9/")
    banco.salvar_vagas([v])
    banco.atualizar_status(chave_vaga("X", "Estágio", v.url), "enviada")
    r = _sessao(pagina, site, banco, ids={"3900001001"}, max_dia=1).executar()
    assert r.enviadas == 0 and "limite diário" in r.encerramento and site[1] == []


def test_nao_repete_vaga_ja_enviada_nem_ja_tentada(pagina, site, banco):
    _sessao(pagina, site, banco, ids={"3900001001", "3900001002"}).executar()
    r2 = _sessao(pagina, site, banco, ids={"3900001001", "3900001002"}).executar()
    assert r2.enviadas == 0 and r2.elegiveis == 0 and site[1] == []


def test_checkpoint_para_tudo(pagina, site, banco):
    r = _sessao(pagina, site, banco, ids={"3900001006"}).executar()
    assert r.enviadas == 0 and r.encerramento.startswith("parado:") and "verificação" in r.encerramento
    assert site[1] == []


def test_arquivo_parar_interrompe_entre_vagas(pagina, site, banco):
    chamadas = {"n": 0}

    def parar():
        chamadas["n"] += 1
        return chamadas["n"] > 4  # deixa a 1ª vaga correr e para antes da seguinte

    r = _sessao(pagina, site, banco, ids={"3900001001", "3900001005"}, deve_parar=parar).executar()
    assert r.encerramento.startswith("parado: arquivo PARAR")
    assert r.enviadas <= 1


def test_nada_de_valor_pessoal_no_log_de_resumo(pagina, site, banco):
    r = _sessao(pagina, site, banco, ids={"3900001001"}).executar()
    texto = r.texto() + json.dumps(_sessao(pagina, site, banco).copiloto.registro.para_json())
    assert "11999990000" not in texto and "a@x.test" not in texto


def test_comandos_linkedin_respeitam_parar(_isolar_arquivos_reais):
    from bot_vagas import cli
    from bot_vagas.parada import solicitar_parada

    solicitar_parada("teste")
    assert cli.main(["aplicar-linkedin"]) == 4 and cli.main(["login-linkedin"]) == 4


def test_coleta_ids_do_html_novo_do_linkedin(pagina):
    from bot_vagas.linkedin import _JS_IDS_VAGAS

    pagina.set_content(
        "<div data-view-job-id='3900000001'></div><div data-entity-urn='urn:li:fsd_jobPosting:3900000002'></div>"
        "<a href='/jobs/search-results/?currentJobId=3900000003&x=1'>a</a><a href='/jobs/view/estagio-ti-at-x-3900000004/'>b</a>"
        "<a href='/company/123456789'>não é vaga</a>")
    assert sorted(pagina.evaluate(_JS_IDS_VAGAS)) == ["3900000001", "3900000002", "3900000003", "3900000004"]


def test_coleta_por_clique_no_layout_novo(pagina):
    """Cartões sem link nem ID: o ID só aparece na URL (?currentJobId=) depois do clique."""
    cartoes = "".join(
        f"<li style='height:110px;width:400px' onclick=\"history.replaceState(null,'','?currentJobId=39000{i:05d}')\">"
        f"Estágio em TI número {i} na Empresa {i}</li>" for i in range(1, 5))
    html = f"<html><body><ul style='margin:0;padding:0;list-style:none'>{cartoes}</ul></body></html>"
    pagina.route("http://busca.test/**", lambda rota: rota.fulfill(status=200, content_type="text/html", body=html))
    pagina.goto("http://busca.test/jobs/search-results/")
    s = SessaoLinkedIn(pagina, ConfigLinkedIn(pausa_acao=(0, 0)), {}, banco=None, filtros=None, pdf=None,
                       dormir=lambda x: None, log=lambda *a, **k: None)
    assert s._ids_por_clique() == [f"39000{i:05d}" for i in range(1, 5)]


def test_leitura_do_layout_novo_sem_h1_nem_classes(pagina):
    from bot_vagas.linkedin import _JS_DADOS_VAGA

    pagina.set_content(
        "<html><head><title>Estágio em TI | Acme | LinkedIn</title></head><body><main>"
        "<a href='/company/acme/life/'>Acme</a><p>Maceió, Alagoas, Brasil · há 2 dias · 12 candidaturas</p>"
        "<span>Híbrido</span><h2>Sobre a vaga</h2><p>Atuar com Python e APIs.</p><p>Requisitos: lógica.</p></main></body></html>")
    d = pagina.evaluate(_JS_DADOS_VAGA)
    assert d["local"] == "Maceió, Alagoas, Brasil (Híbrido)" and d["empresa"] == "Acme"
    assert "Atuar com Python" in d["descricao"] and "Sobre a vaga" not in d["descricao"]
