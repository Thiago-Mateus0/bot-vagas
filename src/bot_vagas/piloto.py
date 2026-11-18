"""Piloto (modo 100% AUTOMÁTICO) para candidaturas na Gupy. É a ÚNICA parte do bot que clica.

Decisão do Thiago em 2026-11-16, com risco de banimento aceito. O piloto:
- abre cada vaga elegível no Chromium com o perfil persistente (sessão do `login-gupy`);
- clica em "Candidatar-se" / "Próximo" / "Continuar"; preenche com respostas.yaml (reusa o reconhecimento do
  copiloto); anexa o PDF; marca termos/LGPD obrigatórios (se `aceitar_termos`); responde perguntas abertas
  com o Haiku (se ligado e com orçamento), checadas pelo verificador de fatos;
- em SIMULAÇÃO para na tela final (botão "Enviar/Finalizar candidatura"), tira screenshot e NÃO clica;
- em ENVIO clica no botão final, espera a confirmação e tira screenshot.

O piloto PULA a vaga (e registra o motivo) quando: campo obrigatório que não sabe responder, pergunta aberta
sem resposta segura, saiu da Gupy (ATS externo), não achou botão, ficou travado na mesma tela, já se candidatou.
O piloto PARA A RODADA (e avisa no celular) quando: tela de login, captcha, sinal de bloqueio, PARAR, Ctrl+C.

Nunca: resolver captcha, evasão de detecção (stealth, UA falso, navigator.webdriver), inventar dado pessoal,
mandar respostas.yaml ou contato ao modelo.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

from . import sessao_sso
from .copiloto import (_JS_TEM_LOGIN, MOTIVO_JANELA, ALVOS_PRODUCAO, ATR_ID, MOTIVO_CTRL_C, MOTIVO_PARAR, MOTIVO_SIGTERM, Alvos,
                       Copiloto, LogCampos, _descricao_campo, _Encerramento, _instalar_sinais, _norm, _texto_campo, abrir_contexto_persistente,
                       classificar_campo, preparar_pasta_perfil)

# --- textos de botões e de tela (normalizados: minúsculas, sem acento) ----------------------------------

RE_FINAL = re.compile(r"^(enviar|finalizar|concluir|confirmar)( a| minha)?( candidatura| inscricao| aplicacao)?$|"
                      r"^submit( application)?$|^send application$")
RE_AVANCAR = re.compile(r"^(proximo|proxima( etapa)?|continuar|avancar|seguir|ir para a proxima etapa|"
                        r"salvar e (continuar|avancar)|next|continue)\b")
RE_INICIAR = re.compile(r"^(candidatar[- ]?se|quero me candidatar|candidate[- ]se|me candidatar|candidatar|"
                        r"inscrever[- ]?se|apply( now)?|aplicar)( (a|para|nesta|na) vaga)?$")
RE_SUCESSO = re.compile(r"candidatura (foi )?(enviada|realizada|concluida|finalizada|recebida)|"
                        r"inscricao (realizada|concluida)|parabens.{0,60}candidat|"
                        r"application (was )?(submitted|sent|received)")
RE_JA_CANDIDATADO = re.compile(r"voce ja (se candidatou|esta participando)|ja se candidatou|"
                               r"candidatura ja (realizada|enviada)|already applied")
RE_BLOQUEIO = re.compile(r"acesso negado|access denied|too many requests|muitas (requisicoes|solicitacoes)|"
                         r"unusual traffic|verify you are human|attention required")
# banner de cookies: o bot RECUSA (só cookies necessários). Sem isso o banner cobre botões e os cliques falham.
RE_BOTAO_ACESSAR = re.compile(r"^(acessar( minha)? conta|entrar|fazer login|login|acessar|sign in|log in)$")
RE_BOTAO_CONTINUAR = re.compile(r"^(continuar|proximo|proxima|avancar|seguir|next|continue)$")
# Marca os campos de login visíveis (data-bot-vagas-login=ident|senha) e diz quais existem. Nunca devolve valores.
_JS_CAMPOS_LOGIN = r"""
() => {
  const vis = e => { const r = e.getBoundingClientRect(), s = getComputedStyle(e);
                     return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };
  document.querySelectorAll('[data-bot-vagas-login]').forEach(e => e.removeAttribute('data-bot-vagas-login'));
  const senha = Array.from(document.querySelectorAll('input[type=password]')).filter(vis);
  // identificador = campo de e-mail/CPF/usuário (nunca um campo de código de verificação)
  const rotulo = e => [e.name, e.id, e.placeholder, e.getAttribute('aria-label'), e.autocomplete,
                       ...Array.from(e.labels || []).map(l => l.innerText)].join(' ').toLowerCase();
  const ident = Array.from(document.querySelectorAll(
      'input[type=email], input[type=text], input[type=tel], input:not([type]), input[autocomplete=username]'))
      .filter(vis)
      .filter(e => e.type === 'email' || /e-?mail|cpf|login|usu[aá]rio|username/.test(rotulo(e)))
      .filter(e => !/c[oó]digo|code|token|otp|verifica/.test(rotulo(e)));
  if (senha[0]) senha[0].setAttribute('data-bot-vagas-login', 'senha');
  if (ident[0]) ident[0].setAttribute('data-bot-vagas-login', 'ident');
  return {tem_senha: senha.length > 0, tem_ident: ident.length > 0};
}
"""
RE_BOTAO_LINKEDIN = re.compile(r"^((entrar|continuar|acessar|login|sign in) (com|with) )?linkedin$")
RE_COOKIES_RECUSAR = re.compile(r"^(rejeitar|recusar)( todos| tudo| cookies| os cookies)?$|^reject all$|"
                                r"^(somente|apenas|usar apenas) (os )?(cookies )?(necessarios|essenciais)$")
RE_MARKETING = re.compile(r"novidades|newsletter|ofertas|promoc|comunicac(oes|ao) de marketing|marketing")
# tela de login reconhecida pelo ENDEREÇO: a da Gupy (/candidates/signin) pede só o e-mail primeiro, sem campo
# de senha, e chega em branco (SPA carregando). Sem isso o bot achava "nenhum botão" ou clicava em "Continuar".
RE_URL_LOGIN = re.compile(r"/(candidates/)?(signin|sign-in|login|entrar)(\.html)?/?$", re.I)
ESPERA_RENDER_MS = 15000   # até quanto esperar a página sair do "em branco"
ESPERA_REOLHAR_MS = 4000  # sem botão: espera isso e olha a tela de novo, uma vez
ESPERA_SSO_MS = 6000       # na tela de login, quanto esperar um redirecionamento automático (sessão já existente)
SEL_CAPTCHA = ('iframe[src*="recaptcha"], iframe[src*="hcaptcha"], iframe[src*="turnstile"], '
               'iframe[src*="challenges.cloudflare"], .g-recaptcha, .h-captcha, .cf-turnstile, [data-sitekey]')

ATR_BOTAO = "data-bot-vagas-botao"

_JS_BOTOES = r"""
(ATR) => {
  const vis = (el) => { const cs = getComputedStyle(el); const r = el.getBoundingClientRect();
    return cs.display !== 'none' && cs.visibility !== 'hidden' && r.width > 0 && r.height > 0; };
  let seq = window.__botVagasBotaoSeq || 0; const out = [];
  document.querySelectorAll('button, [role=button], a, input[type=submit], input[type=button]').forEach(el => {
    if (el.closest('#bot-vagas-painel') || !vis(el)) return;
    const t = (el.innerText || el.value || el.getAttribute('aria-label') || '').replace(/\s+/g, ' ').trim();
    if (!t || t.length > 80) return;
    if (!el.getAttribute(ATR)) { seq += 1; el.setAttribute(ATR, 'b' + seq); }
    out.push({id: el.getAttribute(ATR), texto: t,
              desabilitado: !!(el.disabled || el.getAttribute('aria-disabled') === 'true')});
  });
  window.__botVagasBotaoSeq = seq; return out;
}
"""


# --- campos: copiloto + o que só o modo automático faz ----------------------------------------------------

class CamposAutomaticos(Copiloto):
    """Reconhecimento/preenchimento do copiloto, mais: marca termos obrigatórios, responde perguntas abertas
    (via `responder_aberta`) e marca radios/checkboxes com clique real. Guarda em `bloqueios` os campos
    OBRIGATÓRIOS que ficaram sem resposta (o piloto pula a vaga)."""

    def __init__(self, *a, aceitar_termos: bool = True,
                 responder_aberta: Callable[[str], str | None] | None = None, **kw):
        super().__init__(*a, **kw)
        self.aceitar_termos = aceitar_termos
        self.responder_aberta = responder_aberta
        self.bloqueios: list[str] = []

    TITULO_PAINEL = "bot-vagas (MODO AUTOMÁTICO) — o bot clica e avança sozinho"

    def _marcar_opcao(self, pagina, cid: str) -> bool:
        self._com_flag(pagina, lambda: self._loc(pagina, cid).check())  # modo automático: clique real
        return True

    def _pendente(self, pagina, c, url, motivo):
        super()._pendente(pagina, c, url, motivo)
        if c.get("required"):
            self.bloqueios.append(f"{_descricao_campo(c)}: {motivo}"[:160])

    def _tratar(self, pagina, c, url):
        try:
            return self._tratar_auto(pagina, c, url)
        except Exception as e:
            if c.get("required"):  # campo obrigatório que deu erro: nunca seguir com ele vazio
                self.bloqueios.append(f"{_descricao_campo(c)}: falha ao preencher ({type(e).__name__})"[:160])
            raise

    def _tratar_auto(self, pagina, c, url):
        if not (c["feito"] or c["disabled"] or c["editado"]):
            texto = _texto_campo(c)
            categoria = classificar_campo(c)
            c["_categoria"] = categoria
            cid = c["id"]
            if (categoria == "consentimento" and self.aceitar_termos and c["tipo"] == "checkbox" and not c["checked"]
                    and (c["required"] or not RE_MARKETING.search(_norm(texto)))):
                self._marcar_opcao(pagina, cid)
                self._marcar(pagina, cid, "preenchido", "bot-vagas aceitou (modo automático)")
                self.registro._add(self.registro.preenchidos, (c.get("label") or "termos")[:60], chave="consentimento",
                                   como="aceite automático", url=url)
                c["_motivo"] = "termos/LGPD aceitos (aceitar_termos: true)"
                return "preenchido"
            if categoria == "aberta" and not c["value"] and c["tipo"] in ("textarea", "text") and (
                    c["required"] or c["tag"] == "textarea"):
                pergunta = (c.get("label") or c.get("aria") or c.get("placeholder") or c.get("proximo") or "").strip()
                try:
                    resposta = self.responder_aberta(pergunta) if (self.responder_aberta and pergunta) else None
                except Exception:  # resposta descartada/erro de rede: a vaga é pulada pelo campo vazio
                    resposta = None
                if resposta:
                    self._com_flag(pagina, lambda: self._loc(pagina, cid).fill(resposta))
                    self._marcar(pagina, cid, "preenchido", "bot-vagas respondeu com o modelo — confira")
                    self.registro._add(self.registro.preenchidos, pergunta[:60], chave="pergunta_aberta",
                                       como="modelo (verificado)", url=url)
                    c["_motivo"] = "pergunta aberta respondida pelo modelo e aprovada no verificador"
                    return "preenchido"
                if not c["required"]:
                    c["_motivo"] = "pergunta aberta opcional sem resposta: deixada vazia"
                    self._marcar(pagina, cid, "", "", feito=True)
                    return ""
        return super()._tratar(pagina, c, url)


# --- uma vaga ---------------------------------------------------------------------------------------

@dataclass
class ResultadoVaga:
    resultado: str  # "enviada" | "simulada" | "pulada" | "incerta" | "parar_rodada"
    motivo: str
    capturas: list[str] = field(default_factory=list)
    telas: int = 0
    botoes: list[list[str]] = field(default_factory=list)  # textos dos botões vistos por tela (calibração)
    evento: str = ""  # evento de notificação quando para a rodada: "login" | "alarme_fonte"
    url_final: str = ""  # onde parou (sem query/fragmento), para diagnóstico
    sso: tuple[str, str] | None = None  # ("passou"|"caiu", provedor) do "Entrar com LinkedIn/Google"


def _texto_pagina(pg) -> str:
    try:
        return _norm(pg.evaluate("() => (document.body ? document.body.innerText : '').slice(0, 20000)"))
    except Exception:
        return ""


def _capturar(pg, pasta: Path, nome: str) -> str | None:
    try:
        pasta.mkdir(parents=True, exist_ok=True, mode=0o700)
        destino = pasta / f"{nome}-{datetime.now().strftime('%Y%m%d-%H%M%S')}.png"
        n = 1
        while destino.exists():
            n += 1
            destino = destino.with_name(f"{destino.stem}-{n}.png")
        pg.screenshot(path=str(destino), full_page=True)
        destino.chmod(0o600)  # o print mostra dados seus (formulário preenchido, e-mail na tela de login)
        return str(destino)
    except Exception:
        return None


def _eh_web(pg) -> bool:
    try:
        return not pg.is_closed() and urlsplit(pg.url).scheme in ("http", "https")
    except ValueError:
        return False


def _pagina_ativa(ctx, atual, alvos: Alvos):
    """Se o clique abriu uma aba nova, segue nela. Ignora abas fechadas e em branco (about:blank,
    chrome-error://...): a Gupy abre e fecha uma about:blank na tela do formulário."""
    web = [p for p in ctx.pages if _eh_web(p)]
    if not web:
        abertas = [p for p in ctx.pages if not p.is_closed()]
        return atual if not atual.is_closed() else (abertas[-1] if abertas else atual)
    if atual in web and web[-1] is atual:
        return atual
    for p in reversed(web):
        if p is not atual:
            return p
    return atual


def _dormir(ctx, ms: int) -> None:
    """Espera deixando o Playwright processar eventos (abas novas/fechadas); time.sleep não processa."""
    for p in list(ctx.pages):
        try:
            if not p.is_closed():
                p.wait_for_timeout(ms)
                return
        except Exception:
            continue
    time.sleep(ms / 1000)


def _aba_fechada(e: BaseException) -> bool:
    nome = type(e).__name__
    msg = str(e).lower()
    return nome == "TargetClosedError" or "target page, context or browser has been closed" in msg \
        or "target closed" in msg or "execution context was destroyed" in msg


def _assinatura(pg, botoes) -> tuple:
    u = urlsplit(pg.url)
    try:
        campos = pg.evaluate("() => Array.from(document.querySelectorAll('input,select,textarea'))"
                             ".map(e => (e.name||e.id||e.type)).join('|')")
    except Exception:
        campos = ""
    return (u.netloc, u.path, campos, tuple(b["texto"] for b in botoes))


def _esperar_pagina(pg, ms: int = 800) -> None:
    try:
        pg.wait_for_load_state("domcontentloaded", timeout=15000)
    except Exception:
        pass
    try:
        pg.wait_for_timeout(ms)
    except Exception:
        pass


def _eh_url_de_login(url: str) -> bool:
    try:
        u = urlsplit(url)
    except ValueError:
        return False
    host = (u.hostname or "").lower()
    return bool(RE_URL_LOGIN.search(u.path or "")) or host.startswith(("login.", "auth.", "accounts."))


_JS_TEM_CONTEUDO = r"""
() => {
  const b = document.body;
  if (!b) return false;
  if (document.querySelector('input:not([type=hidden]),select,textarea')) return true;
  return (b.innerText || '').replace(/\s+/g, ' ').trim().length >= 12;  // o branco da Gupy só tem '?'
}
"""


# telas de transição (ex.: depois do LinkedIn, /candidates/auth/linkedin com "Validando suas informações (Isso pode
# levar alguns segundos)"): têm texto, mas ainda não são o formulário. Espera mais, até ESPERA_TRANSICAO_MS.
RE_CARREGANDO = re.compile(r"validando (suas )?informac|carregando|aguarde|redirecionando|processando|"
                           r"isso pode levar alguns segundos|^loading|please wait")
RE_URL_RETORNO_SSO = re.compile(r"/candidates/auth/(linkedin|google|facebook|microsoft|apple)\b", re.I)
NOMES_PROVEDOR = {"linkedin": "LinkedIn", "google": "Google", "facebook": "Facebook", "microsoft": "Microsoft",
                  "apple": "Apple"}
RE_URL_TRANSICAO = re.compile(r"/candidates/auth/|/callback\b|/oauth\b", re.I)
ESPERA_TRANSICAO_MS = 30000


# spinner visível (ex.: MUI CircularProgress da Gupy no /apply) sem nenhum campo na tela = ainda carregando.
# Barra de progresso com aria-valuenow (etapas da candidatura) NÃO conta.
_JS_SPINNER = r"""
() => {
  const vis = e => { const r = e.getBoundingClientRect(), s = getComputedStyle(e);
                     return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };
  const campos = Array.from(document.querySelectorAll('input:not([type=hidden]),select,textarea')).some(vis);
  if (campos) return false;
  const sel = '[role=progressbar]:not([aria-valuenow]),[aria-busy=true],[class*=CircularProgress],' +
              '[class*=spinner],[class*=Spinner],[class*=loading],[class*=Loading]';
  return Array.from(document.querySelectorAll(sel)).some(vis);
}
"""


def _carregando(pg) -> bool:
    try:
        if pg.evaluate(_JS_SPINNER):
            return True
    except Exception:
        pass
    try:
        if RE_URL_TRANSICAO.search(urlsplit(pg.url).path or ""):
            return True
    except ValueError:
        return False
    t = _texto_pagina(pg)
    return len(t) < 300 and bool(RE_CARREGANDO.search(t)) and not RE_SUCESSO.search(t)


def _esperar_render(pg, _redirecionamentos: int = 0) -> bool:
    """Espera a SPA sair do "em branco" (até ESPERA_RENDER_MS) e das telas de transição (até
    ESPERA_TRANSICAO_MS). Se cair numa tela de login, espera mais um pouco: com a sessão já salva, a Gupy
    redireciona sozinha para o formulário. Devolve True se a página está pronta (com conteúdo, sem carregar)."""
    def tem_conteudo() -> bool:
        try:
            return bool(pg.evaluate(_JS_TEM_CONTEUDO))
        except Exception:
            return False  # navegando no meio da avaliação

    passo, gasto, limite = 500, 0, ESPERA_RENDER_MS
    while True:
        if _carregando(pg):
            limite = max(limite, ESPERA_TRANSICAO_MS)
        elif tem_conteudo():
            break
        if gasto >= limite:
            break
        try:
            pg.wait_for_timeout(passo)
        except Exception:
            pass
        gasto += passo
    if _eh_url_de_login(pg.url):
        for _ in range(max(1, ESPERA_SSO_MS // passo)):
            try:
                pg.wait_for_timeout(passo)
            except Exception:
                pass
            if not _eh_url_de_login(pg.url):
                _esperar_pagina(pg, 300)
                if _redirecionamentos < 2:
                    return _esperar_render(pg, _redirecionamentos + 1)
                break
    return tem_conteudo() and not _carregando(pg)


# "Entrar com ..." da Gupy: quando a sessão do provedor existe no perfil do bot, ele devolve sozinho para a Gupy.
PROVEDORES_LOGIN = (
    ("LinkedIn", re.compile(r"(^|\.)linkedin\.com$"), re.compile(r"^/(uas/|oauth/|checkpoint/|login|authwall)")),
    ("Google", re.compile(r"^accounts\.google\.com$"), re.compile(r"")),
    ("Facebook", re.compile(r"(^|\.)facebook\.com$"), re.compile(r"^/(login|dialog/oauth|v[\d.]+/dialog/oauth)")),
    ("Microsoft", re.compile(r"^login\.(microsoftonline|live)\.com$"), re.compile(r"")),
    ("Apple", re.compile(r"^appleid\.apple\.com$"), re.compile(r"")),
)


def provedor_de_login_externo(url: str) -> str | None:
    try:
        u = urlsplit(url)
    except ValueError:
        return None
    host = (u.hostname or "").lower()
    for nome, rx_host, rx_path in PROVEDORES_LOGIN:
        if rx_host.search(host) and rx_path.search(u.path or "/"):
            return nome
    return None


_JS_FORM_VISIVEL = r"""
() => Array.from(document.querySelectorAll('input:not([type=hidden])')).some(e => {
  const r = e.getBoundingClientRect(), s = getComputedStyle(e);
  return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
})
"""


def _esperar_voltar_para_gupy(ctx, pg, campos, ms: int | None = None):
    """Saiu da Gupy (ex.: OAuth do LinkedIn): espera o redirecionamento automático de volta.
    - site qualquer: até ESPERA_SSO_MS;
    - login de provedor (LinkedIn/Google...): até ESPERA_TRANSICAO_MS enquanto a página estiver em branco/
      carregando (caso real: linkedin.com/uas/login em branco por mais de 6 s com a sessão VÁLIDA); se aparecer
      formulário de login, desiste depois de ESPERA_SSO_MS (a sessão caiu de verdade)."""
    passo = 500
    curto = ESPERA_SSO_MS if ms is None else ms
    longo = max(curto, ESPERA_TRANSICAO_MS)
    gasto = 0
    while True:
        try:
            pg.wait_for_timeout(passo)
        except Exception:
            pass
        gasto += passo
        pg = _pagina_ativa(ctx, pg, campos.alvos)
        if campos.url_permitida(pg.url):
            _esperar_pagina(pg, 300)
            return pg
        if gasto < curto:
            continue
        if gasto >= longo or not provedor_de_login_externo(pg.url):
            return pg
        try:
            if pg.evaluate(_JS_FORM_VISIVEL):
                return pg  # o provedor está pedindo login: não adianta esperar
        except Exception:
            pass  # navegando: continua esperando


def candidatar_uma(ctx, url: str, campos: CamposAutomaticos, *, enviar: bool, capturas: Path, nome: str,
                   max_telas: int = 15, parar: Callable[[], bool] = lambda: False, log=print,
                   entrar_com_linkedin: bool = False, credenciais: Callable[[], Any] | None = None,
                   ancora=None) -> ResultadoVaga:
    alvos = campos.alvos
    pg = ctx.new_page()
    res = ResultadoVaga("pulada", "")
    try:
        try:
            pg.goto(url, wait_until="domcontentloaded")
        except Exception as e:
            # a Gupy pode fechar a aba original e seguir numa nova (window.open + window.close) já no carregamento
            if not _aba_fechada(e):
                raise
            _dormir(ctx, 1500)
            pg = _pagina_ativa(ctx, pg, alvos)
        clicou_linkedin = False
        login_digitado = {"ident": 0, "senha": 0}
        reaberturas = 0
        reabriu_pos_login = False
        repetidas, ultima = 0, None
        reesperou = False
        clicou_final = False
        fechadas = 0
        for tela in range(1, max_telas + 1):
            try:
                res.telas = tela
                if parar():
                    return _fim(res, "parar_rodada", MOTIVO_PARAR)
                pg = _pagina_ativa(ctx, pg, alvos)
                vivas = lambda: [x for x in ctx.pages if not x.is_closed() and x is not ancora]  # noqa: E731
                if pg.is_closed() or not vivas():
                    _dormir(ctx, 1500)  # pode ser uma aba nova abrindo
                    pg = _pagina_ativa(ctx, pg, alvos)
                if not vivas():
                    # caso real: depois do login pelo LinkedIn a Gupy FECHA a aba (window.close). A sessão já vale:
                    # reabre a vaga numa aba nova (no máximo 2 vezes por vaga).
                    if reaberturas >= 2:
                        return _fim(res, "nao_carregou", "a Gupy fechou a aba da candidatura de novo; tento na próxima rodada")
                    reaberturas += 1
                    log(f"  tela {tela}: a Gupy fechou a aba; reabrindo a vaga")
                    pg = ctx.new_page()
                    pg.goto(url, wait_until="domcontentloaded")
                _esperar_pagina(pg)
                pg = _pagina_ativa(ctx, pg, alvos)
                if not campos.url_permitida(pg.url):
                    pg = _esperar_voltar_para_gupy(ctx, pg, campos)
                if campos.url_permitida(pg.url):
                    m = RE_URL_RETORNO_SSO.search(urlsplit(pg.url).path or "")
                    if m and res.sso is None:
                        res.sso = ("passou", NOMES_PROVEDOR[m.group(1).lower()])  # voltou logado sem ninguém digitar
                    carregou = _esperar_render(pg)
                res.url_final = _sem_query(pg.url)
                if not campos.url_permitida(pg.url):
                    url_fora = pg.url  # mesma URL para host, provedor e registro (a página pode navegar no print)
                    host = urlsplit(url_fora).hostname or "?"
                    provedor = provedor_de_login_externo(url_fora)
                    res.capturas.append(_capturar(pg, capturas, nome) or "")
                    if provedor:
                        res.sso = ("caiu", provedor)
                        # "Entrar com LinkedIn/Google": a sessão do provedor não está no perfil do bot. Nada digitado.
                        return _fim(res, "parar_rodada", f"a Gupy mandou para o login do {provedor} ({host}): rode "
                                    "`bot-vagas login-gupy <link da vaga>`, clique em Candidatar-se e faça login no "
                                    f"{provedor} nessa janela", "login")
                    return _fim(res, "pulada", f"a candidatura continua fora da Gupy ({host}); faça à mão")
                if pg.locator(SEL_CAPTCHA).count():
                    res.capturas.append(_capturar(pg, capturas, nome) or "")
                    return _fim(res, "parar_rodada", "captcha na página: o bot não resolve captcha", "login")
                texto = _texto_pagina(pg)
                if RE_BLOQUEIO.search(texto):
                    res.capturas.append(_capturar(pg, capturas, nome) or "")
                    return _fim(res, "parar_rodada", "sinal de bloqueio da Gupy: rodada interrompida, nada contornado",
                                "alarme_fonte")
                if clicou_final or RE_SUCESSO.search(texto):
                    if clicou_final and RE_SUCESSO.search(texto):
                        res.capturas.append(_capturar(pg, capturas, nome + "-enviada") or "")
                        return _fim(res, "enviada", "candidatura enviada (confirmação vista na página)")
                    if not clicou_final:
                        return _fim(res, "pulada", "a página já diz que a candidatura foi feita")
                if RE_JA_CANDIDATADO.search(texto):
                    return _fim(res, "pulada", "você já se candidatou a esta vaga")
                if pg.evaluate(_JS_TEM_LOGIN) or _eh_url_de_login(pg.url):
                    if credenciais is not None:
                        if login_digitado["senha"]:
                            res.capturas.append(_capturar(pg, capturas, nome) or "")
                            return _fim(res, "parar_rodada", "a Gupy continuou pedindo login depois de eu digitar a "
                                        "senha do chaveiro (senha errada, código ou verificação?). Confira com "
                                        "`bot-vagas senha-gupy` ou faça login com `bot-vagas login-gupy <link>`", "login")
                        motivo = _login_digitando(pg, credenciais, login_digitado, campos, log, tela)
                        if motivo is None:
                            continue
                        res.capturas.append(_capturar(pg, capturas, nome) or "")
                        return _fim(res, "parar_rodada", motivo, "login")
                    if entrar_com_linkedin and not clicou_linkedin:
                        social = next((b for b in pg.evaluate(_JS_BOTOES, ATR_BOTAO)
                                       if not b["desabilitado"] and RE_BOTAO_LINKEDIN.search(_norm(b["texto"]))), None)
                        if social is not None:
                            # só escolhe o provedor; quem autentica é a sessão do LinkedIn já salva no perfil.
                            # Nenhum campo é tocado. Se o LinkedIn pedir senha, a rodada para (regra B).
                            clicou_linkedin = True
                            log(f"  tela {tela}: login da Gupy pelo botão '{social['texto']}' (sessão salva; nada digitado)")
                            pg.locator(f"[{ATR_BOTAO}='{social['id']}']").first.click()
                            continue
                    res.capturas.append(_capturar(pg, capturas, nome) or "")
                    host = urlsplit(pg.url).hostname or "?"
                    if clicou_linkedin and not reabriu_pos_login:
                        # a validação pode ter terminado em outra aba (que a Gupy fecha); a sessão já deve valer
                        reabriu_pos_login = True
                        log(f"  tela {tela}: voltou para o login depois do LinkedIn; reabrindo a vaga")
                        pg.goto(url, wait_until="domcontentloaded")
                        continue
                    if clicou_linkedin:
                        # o LinkedIn autenticou (passou por /candidates/auth/linkedin), mas a Gupy voltou para o login
                        return _fim(res, "parar_rodada", f"a Gupy não aceitou o login pelo LinkedIn (em {host}: validou "
                                    "e voltou para 'Entrar com sua conta'); teste à mão com `bot-vagas login-gupy "
                                    "<link da vaga>` e o botão LinkedIn", "login")
                    return _fim(res, "parar_rodada", f"a Gupy pediu login (em {host}): rode `bot-vagas login-gupy "
                                "<link da vaga>`, clique em Candidatar-se e faça login", "login")
                _recusar_cookies(pg)

                campos.bloqueios.clear()
                campos.passo(pg)
                if campos.bloqueios:
                    res.capturas.append(_capturar(pg, capturas, nome) or "")
                    return _fim(res, "pulada", "campo obrigatório sem resposta segura: " + "; ".join(campos.bloqueios[:3]))

                botoes = [b for b in pg.evaluate(_JS_BOTOES, ATR_BOTAO) if not b["desabilitado"]]
                res.botoes.append([b["texto"] for b in botoes][:25])
                achar = lambda rx: next((b for b in botoes if rx.search(_norm(b["texto"]))), None)  # noqa: E731
                final, avancar, iniciar = achar(RE_FINAL), achar(RE_AVANCAR), achar(RE_INICIAR)

                if clicou_final:
                    # clicou em enviar, a página mudou, mas não apareceu confirmação: NÃO tenta de novo
                    res.capturas.append(_capturar(pg, capturas, nome + "-sem-confirmacao") or "")
                    return _fim(res, "incerta", "cliquei em enviar e não vi confirmação: confira na Gupy")

                if final is not None:
                    res.capturas.append(_capturar(pg, capturas, nome + ("-antes-de-enviar" if enviar else "-simulada")) or "")
                    if not enviar:
                        return _fim(res, "simulada", f"simulação: parou no botão final '{final['texto']}' sem clicar")
                    log(f"  enviando: clique em '{final['texto']}'")
                    pg.locator(f"[{ATR_BOTAO}='{final['id']}']").first.click()
                    clicou_final = True
                    for _ in range(10):  # até ~10 s esperando a confirmação
                        _esperar_pagina(pg, 1000)
                        pg = _pagina_ativa(ctx, pg, alvos)
                        if RE_SUCESSO.search(_texto_pagina(pg)):
                            break
                    continue
                alvo = avancar or iniciar
                if alvo is None and not reesperou:
                    # SPA ainda montando a tela: olha de novo uma vez antes de desistir
                    reesperou = True
                    try:
                        pg.wait_for_timeout(ESPERA_REOLHAR_MS)
                    except Exception:
                        pass
                    continue
                if alvo is None:
                    res.capturas.append(_capturar(pg, capturas, nome) or "")
                    if not carregou or _carregando(pg):
                        return _fim(res, "nao_carregou", f"a página não carregou em {ESPERA_RENDER_MS // 1000} s; "
                                    "tento de novo na próxima rodada")
                    return _fim(res, "pulada", "não achei botão de candidatar/avançar/enviar nesta tela")
                assinatura = _assinatura(pg, botoes)
                repetidas = repetidas + 1 if assinatura == ultima else 0
                ultima = assinatura
                if repetidas >= 2:
                    res.capturas.append(_capturar(pg, capturas, nome) or "")
                    return _fim(res, "pulada", f"travado na mesma tela depois de clicar em '{alvo['texto']}' (erro de validação?)")
                reesperou = False
                log(f"  tela {tela}: clique em '{alvo['texto']}'")
                pg.locator(f"[{ATR_BOTAO}='{alvo['id']}']").first.click()
            except Exception as e:
                # aba fechada no meio da tela (a Gupy abre e fecha uma aba about:blank no formulário):
                # volta para a aba certa e olha a tela de novo, no máximo 3 vezes por vaga
                if not _aba_fechada(e) or fechadas >= 3:
                    raise
                fechadas += 1
                _dormir(ctx, 1500)
                pg = _pagina_ativa(ctx, pg, alvos)
                continue
        res.capturas.append(_capturar(pg, capturas, nome) or "")
        return _fim(res, "incerta" if clicou_final else "pulada", f"mais de {max_telas} telas sem terminar")
    finally:
        res.capturas = [c for c in res.capturas if c]
        for p in list(ctx.pages):
            try:
                if p is ancora or p.is_closed():
                    continue
                if ancora is not None or len(ctx.pages) > 1:
                    p.close()
            except Exception:
                pass


def _login_digitando(pg, credenciais, feito: dict, campos, log, tela: int) -> str | None:
    """Tela de login da GUPY: digita login (e-mail/CPF) e senha do chaveiro e clica em 'Acessar conta'
    (ou 'Continuar', quando a Gupy pede primeiro só o e-mail). Decisão do Thiago (2026-11-17).
    Só em *.gupy.io; a senha é digitada no máximo UMA vez por vaga (o piloto para a rodada se pedir de novo).
    Nunca registra login/senha em log, banco ou mensagem. Devolve None se clicou, ou o motivo para parar."""
    if not campos.url_permitida(pg.url):
        return "tela de login fora da Gupy: o bot não digita senha fora de gupy.io"
    try:
        estado = pg.evaluate(_JS_CAMPOS_LOGIN)
    except Exception:
        return "não consegui ler a tela de login da Gupy; faça login com `bot-vagas login-gupy <link da vaga>`"
    if not estado.get("tem_ident") and not estado.get("tem_senha"):
        return ("a Gupy pediu login sem campo de e-mail/senha (código, link por e-mail ou verificação): "
                "faça login com `bot-vagas login-gupy <link da vaga>`")
    if not estado.get("tem_senha") and feito["ident"]:
        return "a Gupy pediu o e-mail de novo sem mostrar o campo de senha: faça login com `bot-vagas login-gupy <link>`"
    try:
        cred = credenciais()
    except Exception as e:  # chaveiro trancado/indisponível (a mensagem não tem segredo)
        return f"não consegui ler a senha da Gupy no chaveiro do sistema ({type(e).__name__}); a sessão está bloqueada?"
    if not cred:
        return "a senha da Gupy não está no chaveiro: rode `bot-vagas senha-gupy` uma vez"
    login, senha = cred
    try:
        if estado.get("tem_ident"):
            pg.locator("[data-bot-vagas-login=ident]").first.fill(login)
        if estado.get("tem_senha"):
            pg.locator("[data-bot-vagas-login=senha]").first.fill(senha)
    except Exception:
        return "não consegui preencher a tela de login da Gupy; faça login com `bot-vagas login-gupy <link da vaga>`"
    finally:
        del login, senha, cred  # não deixa o segredo pendurado em variável
    rx = RE_BOTAO_ACESSAR if estado.get("tem_senha") else RE_BOTAO_CONTINUAR
    ativo = None
    for _ in range(8):  # a Gupy libera o botão depois de validar os campos
        botoes = [b for b in pg.evaluate(_JS_BOTOES, ATR_BOTAO) if rx.search(_norm(b["texto"]))]
        ativo = next((b for b in botoes if not b["desabilitado"]), None)
        if ativo is not None:
            break
        pg.wait_for_timeout(400)
    if ativo is None:
        return "a Gupy não liberou o botão de entrar depois do preenchimento; faça login com `bot-vagas login-gupy <link>`"
    if estado.get("tem_senha"):
        feito["senha"] += 1
        log(f"  tela {tela}: login da Gupy com a senha do chaveiro: clique em '{ativo['texto']}' (nada registrado)")
    else:
        feito["ident"] += 1
        log(f"  tela {tela}: login da Gupy: e-mail/CPF do chaveiro e clique em '{ativo['texto']}'")
    pg.locator(f"[{ATR_BOTAO}='{ativo['id']}']").first.click()
    return None


def _fim(res: ResultadoVaga, resultado: str, motivo: str, evento: str = "") -> ResultadoVaga:
    res.resultado, res.motivo, res.evento = resultado, motivo, evento
    return res


def _sem_query(url: str) -> str:
    try:
        u = urlsplit(url)
        return f"{u.scheme}://{u.netloc}{u.path}"
    except ValueError:
        return ""


def _recusar_cookies(pg) -> bool:
    """Clica em 'Rejeitar todos' se houver banner de cookies. Devolve True se clicou."""
    try:
        botoes = pg.evaluate(_JS_BOTOES, ATR_BOTAO)
    except Exception:
        return False
    for b in botoes:
        if not b["desabilitado"] and RE_COOKIES_RECUSAR.search(_norm(b["texto"])):
            try:
                pg.locator(f"[{ATR_BOTAO}='{b['id']}']").first.click(timeout=5000)
                pg.wait_for_timeout(300)
                return True
            except Exception:
                return False
    return False


# --- rodada -----------------------------------------------------------------------------------------

STATUS_POR_RESULTADO = {"enviada": "enviada", "simulada": "simulada", "pulada": "pulada", "incerta": "em_candidatura"}


@dataclass
class Rodada:
    tentativas: int = 0
    por_resultado: dict[str, int] = field(default_factory=dict)
    motivo_fim: str = ""


def executar_rodada(vagas: list, *, banco, cfg, preparar_pdf: Callable[[Any], Path], respostas: dict | None,
                    responder_para: Callable[[Any], Callable[[str], str | None] | None],
                    pasta_perfil: Path, capturas: Path, restantes: Callable[[str], int],
                    alvos: Alvos = ALVOS_PRODUCAO, headless: bool | None = None, canal: str | None = None,
                    parar: Callable[[], bool] = lambda: False, notificar: Callable[[str], Any] = lambda e: None,
                    log_campos: LogCampos | None = None, dormir: Callable[[float], None] | None = None,
                    pausa: Callable[[], float] = lambda: 0.0, log=print,
                    credenciais: Callable[[], Any] | None = None) -> Rodada:
    """Candidata-se às `vagas` (linhas do banco) em sequência, numa única sessão do navegador.
    `restantes(site)` devolve quantas tentativas ainda cabem hoje (limites). Grava tudo no banco."""
    from playwright.sync_api import Error as ErroPW, sync_playwright

    from .automacao import site_da_url

    rodada = Rodada()
    fim = _Encerramento()
    restaurar = _instalar_sinais(fim)
    pasta = preparar_pasta_perfil(pasta_perfil)
    visivel = cfg.visivel() if headless is None else not headless

    def parar_agora() -> bool:
        if fim:
            return True
        if parar():
            fim.pedir(MOTIVO_PARAR)
            return True
        return False

    def esperar(segundos: float) -> None:
        limite = time.monotonic() + segundos
        while time.monotonic() < limite and not parar_agora():
            (dormir or time.sleep)(min(1.0, max(0.0, limite - time.monotonic())))

    try:
        with sync_playwright() as p:
            ctx = abrir_contexto_persistente(p, pasta, headless=not visivel, canal=canal)
            # aba em branco fixa: se a Gupy fechar a aba da vaga (window.close), o navegador não fecha junto
            ancora = ctx.pages[0] if ctx.pages else ctx.new_page()
            fechando = []  # o próprio bot fechando no fim: não é "janela fechada"
            ctx.on("close", lambda *_: None if fechando else fim.pedir(MOTIVO_JANELA))  # você fechou a janela
            if dormir is None:
                dormir = lambda seg: _dormir(ctx, int(seg * 1000))  # noqa: E731 (processa o evento de fechar)
            try:
                for i, row in enumerate(vagas):
                    if parar_agora():
                        break
                    site = site_da_url(row["url"]) or "?"
                    if restantes(site) <= 0:
                        log(f"limite de hoje atingido para {site}; parando.")
                        rodada.motivo_fim = "limite diário"
                        break
                    if i > 0 and rodada.tentativas:
                        s = pausa()
                        log(f"pausa de {s:.0f}s antes da próxima candidatura...")
                        esperar(s)
                        if parar_agora():
                            break
                    r = _uma_vaga(ctx, row, banco=banco, cfg=cfg, site=site, preparar_pdf=preparar_pdf,
                                  respostas=respostas, responder=responder_para(row), alvos=alvos, capturas=capturas,
                                  parar=parar_agora, log_campos=log_campos, log=log, ancora=ancora,
                                  credenciais=credenciais)
                    rodada.por_resultado[r.resultado] = rodada.por_resultado.get(r.resultado, 0) + 1
                    if r.resultado != "parar_rodada":
                        rodada.tentativas += 1
                    else:
                        if r.evento:
                            notificar(r.evento)
                        rodada.motivo_fim = r.motivo
                        break
            finally:
                fechando.append(True)
                try:
                    ctx.close()
                except Exception:
                    pass
    except KeyboardInterrupt:
        fim.pedir(MOTIVO_CTRL_C)
    except ErroPW as e:
        rodada.motivo_fim = rodada.motivo_fim or f"navegador fechado/falhou ({type(e).__name__})"
    finally:
        restaurar()
    if fim:
        rodada.motivo_fim = fim.motivo
    return rodada


def _uma_vaga(ctx, row, *, banco, cfg, site, preparar_pdf, respostas, responder, alvos, capturas, parar,
              log_campos, log, ancora=None, credenciais=None) -> ResultadoVaga:
    chave = row["chave"]
    rotulo = f"{row['empresa']} / {row['cargo']}"
    log(f"\n[{cfg.modo.upper()}] {rotulo}\n  {row['url']}")
    try:
        pdf = preparar_pdf(row)
    except Exception as e:  # currículo não coube, perfil inválido...
        _gravar(banco, chave, row, "pulada", ResultadoVaga("pulada", f"não consegui gerar o PDF ({type(e).__name__})"),
                site, cfg, None)
        log(f"  PULADA: não consegui gerar o PDF ({type(e).__name__})")
        return ResultadoVaga("pulada", "PDF")
    status_anterior = row["status"]
    banco.atualizar_status(chave, "em_candidatura", motivo="modo automático em andamento")
    campos = CamposAutomaticos(respostas, pdf, alvos=alvos, log=lambda *a, **k: None, log_campos=log_campos,
                               aceitar_termos=cfg.aceitar_termos, responder_aberta=responder)
    try:
        r = candidatar_uma(ctx, row["url"], campos, enviar=cfg.envia_de_verdade, capturas=capturas,
                           nome=chave[:12], max_telas=cfg.max_telas_por_vaga, parar=parar, log=log,
                           entrar_com_linkedin=cfg.entrar_com_linkedin,
                           credenciais=credenciais if cfg.digitar_senha_gupy else None, ancora=ancora)
    except Exception as e:
        if parar():  # janela do navegador fechada, Ctrl+C ou PARAR durante a vaga: para a rodada, sem culpar a vaga
            r = ResultadoVaga("parar_rodada", "navegador fechado ou parada pedida durante a vaga")
        else:
            detalhe = re.sub(r"\?\S*", "", str(e).splitlines()[0] if str(e) else "")[:160]  # sem query strings
            r = ResultadoVaga("pulada", f"erro do navegador nesta vaga ({type(e).__name__}: {detalhe}); "
                                        "tento outra vez depois")
        voltar_status = True
    else:
        voltar_status = False
    if r.resultado in ("parar_rodada", "nao_carregou") or voltar_status:
        # nada foi enviado: volta ao status anterior para tentar numa próxima rodada
        _gravar(banco, chave, row, status_anterior, r, site, cfg, campos)
    else:
        _gravar(banco, chave, row, STATUS_POR_RESULTADO[r.resultado], r, site, cfg, campos)
    if r.sso:
        sessao_sso.registrar(*r.sso)
    log(f"  {r.resultado.upper()}: {r.motivo}" + (f"\n  captura: {r.capturas[-1]}" if r.capturas else ""))
    return r


def _gravar(banco, chave, row, status, r: ResultadoVaga, site, cfg, campos: CamposAutomaticos | None) -> None:
    atual = banco.buscar_por_chave(chave) or row
    try:
        d = json.loads(atual["detalhes"]) if atual["detalhes"] else {}
    except ValueError:
        d = {}
    if not isinstance(d, dict):
        d = {}
    auto = d.get("automatico") if isinstance(d.get("automatico"), dict) else {}
    tentativas = auto.get("tentativas") if isinstance(auto.get("tentativas"), list) else []
    agora = datetime.now()
    if r.resultado != "parar_rodada":
        tentativas.append({"dia": agora.date().isoformat(), "site": site, "modo": cfg.modo, "resultado": r.resultado,
                           "quando": datetime.now(timezone.utc).isoformat(timespec="seconds")})
    auto.update({
        "tentativas": tentativas[-20:], "ultimo_resultado": r.resultado, "ultimo_motivo": r.motivo,
        "capturas": r.capturas, "telas": r.telas, "botoes_vistos": r.botoes[-15:], "url_final": r.url_final,
        "campos": campos.registro.para_json() if campos is not None else None,  # só nomes de campos, sem valores
    })
    d["automatico"] = auto
    if r.resultado == "enviada":
        d["marcacao"] = {"status": "enviada", "quando": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                         "anterior": row["status"], "por": "bot (modo automático)"}
    banco.atualizar_status(chave, status, motivo=f"automático: {r.motivo}"[:300],
                           detalhes=json.dumps(d, ensure_ascii=False))
