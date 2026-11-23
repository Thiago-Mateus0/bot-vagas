"""LinkedIn — Candidatura Simplificada (Easy Apply) AUTOMÁTICA, ligada por decisão explícita do Thiago.

ATENÇÃO (decisão de 2026-11-23): os termos de uso do LinkedIn proíbem automação e a conta pode ser
restringida ou banida. O Thiago aceitou esse risco. Por isso este módulo é separado do copiloto da Gupy
(que continua sem clicar em nada) e tem travas próprias:

- o login é SEMPRE feito por você, à mão (`login-linkedin`); o bot nunca lê, guarda ou digita senha/cookies;
- limite diário de envios (linkedin.yaml, padrão 8) e pausas entre ações e entre candidaturas;
- só vagas que passam no filtro (filtros.yaml: estágio/trainee, remoto ou Maceió);
- só envia se TODAS as perguntas obrigatórias tiverem resposta em respostas.yaml; senão desiste daquela vaga
  (descarta o rascunho no LinkedIn) e registra os NOMES dos campos que faltaram;
- nunca marca termos/consentimento, nunca responde pergunta aberta, nunca inventa número (anos de experiência
  só com valor definido em respostas.yaml);
- para tudo e avisa ao ver login/checkpoint/captcha, limite do LinkedIn, ou o arquivo PARAR. Não resolve
  captcha e não faz evasão de detecção (sem stealth, sem mexer em navigator.webdriver, sem UA falso).

Os seletores do LinkedIn NÃO são documentados e mudam sem aviso; ele foi escrito com rótulos acessíveis
(PT/EN) e testado só contra páginas de teste locais, nunca contra o LinkedIn real. Se algo não bater, o bot
desiste da vaga e avisa; confira o primeiro envio real com `--sem-enviar` e depois com `--max 1`.
"""

from __future__ import annotations

import json
import random
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote, urlsplit

import yaml

from . import copiloto as cop
from .banco import Banco, chave_vaga
from .copiloto import (ATR_ID, Alvos, Copiloto, LogCampos, PerfilEmUso, _CapturaSinais, _dormir_ouvindo,
                       _encerrar_navegador, _JS_COLETAR, _norm, abrir_contexto_persistente,
                       perfil_em_uso, preparar_pasta_perfil)
from .filtro import Filtros, avaliar_palavras_chave
from .fontes import Vaga
from .fontes.texto import html_para_texto
from .perfil import RAIZ_PROJETO
from .respostas import _buscar

HOSTS_LINKEDIN: tuple[str, ...] = ("linkedin.com",)
ALVOS_LINKEDIN = Alvos(hosts=HOSTS_LINKEDIN)
URL_LOGIN = "https://www.linkedin.com/login"
LINKEDIN_PADRAO = RAIZ_PROJETO / "linkedin.yaml"
FONTE = "linkedin"
MAX_PASSOS_FORMULARIO = 14
SELETOR_DIALOGO = "[role=dialog]"


# --- configuração --------------------------------------------------------------------------

@dataclass(frozen=True)
class ConfigLinkedIn:
    buscas: tuple[tuple[str, str], ...] = (("estagio ti", "Brasil"),)
    somente_simplificada: bool = True
    nivel_estagio: bool = True
    dias: int = 7
    max_envios_por_dia: int = 8
    max_vagas_por_execucao: int = 40
    pausa_acao: tuple[float, float] = (1.5, 4.0)
    pausa_vaga: tuple[float, float] = (25.0, 70.0)
    falhas_seguidas_para_parar: int = 4
    base: str = "https://www.linkedin.com"  # só os testes mudam (páginas locais)


def carregar_config(caminho: Path | str = LINKEDIN_PADRAO) -> ConfigLinkedIn:
    caminho = Path(caminho)
    if not caminho.exists():
        return ConfigLinkedIn()
    with open(caminho, encoding="utf-8") as f:
        d = yaml.safe_load(f) or {}
    if not isinstance(d, dict):
        raise ValueError(f"linkedin.yaml inválido em {caminho}")
    buscas = []
    for b in d.get("buscas") or []:
        if isinstance(b, dict) and str(b.get("termo", "")).strip():
            buscas.append((str(b["termo"]).strip(), str(b.get("local") or "Brasil").strip()))
        elif isinstance(b, str) and b.strip():
            buscas.append((b.strip(), "Brasil"))
    padrao = ConfigLinkedIn()

    def par(chave: str, atual: tuple[float, float]) -> tuple[float, float]:
        v = d.get(chave)
        if isinstance(v, (list, tuple)) and len(v) == 2:
            a, b = float(v[0]), float(v[1])
            return (min(a, b), max(a, b))
        return atual

    return ConfigLinkedIn(
        buscas=tuple(buscas) or padrao.buscas,
        somente_simplificada=bool(d.get("somente_candidatura_simplificada", True)),
        nivel_estagio=bool(d.get("filtrar_nivel_estagio", True)),
        dias=max(1, int(d.get("publicadas_ultimos_dias", padrao.dias))),
        max_envios_por_dia=max(0, int(d.get("max_envios_por_dia", padrao.max_envios_por_dia))),
        max_vagas_por_execucao=max(1, int(d.get("max_vagas_analisadas_por_execucao", padrao.max_vagas_por_execucao))),
        pausa_acao=par("pausa_entre_acoes_s", padrao.pausa_acao),
        pausa_vaga=par("pausa_entre_candidaturas_s", padrao.pausa_vaga),
        falhas_seguidas_para_parar=max(1, int(d.get("falhas_seguidas_para_parar", padrao.falhas_seguidas_para_parar))),
    )


def url_busca(termo: str, local: str, cfg: ConfigLinkedIn) -> str:
    q = [f"keywords={quote(termo, safe='')}", f"location={quote(local, safe='')}", f"f_TPR=r{cfg.dias * 86400}",
         "sortBy=DD"]
    if cfg.somente_simplificada:
        q.append("f_AL=true")
    if cfg.nivel_estagio:
        q.append("f_E=1")
    return f"{cfg.base}/jobs/search/?" + "&".join(q)


def url_vaga(id_vaga: str, base: str = "https://www.linkedin.com") -> str:
    return f"{base}/jobs/view/{id_vaga}/"


# --- reconhecimento de textos (PT/EN) ---------------------------------------------------------

R_SIMPLIFICADA = re.compile(r"candidatura simplificada|easy apply", re.I)
R_JA_CANDIDATOU = re.compile(r"\b(candidatou-se|candidatura enviada|applied)\b", re.I)
R_PROXIMO = re.compile(r"avan[cç]ar|pr[oó]xim[ao]|continue to next|^next$|^continue$|continuar", re.I)
R_REVISAR = re.compile(r"revis(ar|e)|review", re.I)
R_ENVIAR = re.compile(r"enviar candidatura|submit application|^enviar$|^submit$", re.I)
R_DISPENSAR = re.compile(r"^(dispensar|dismiss|fechar|close)$", re.I)
R_DESCARTAR = re.compile(r"^(descartar|discard)$", re.I)
R_CONCLUIDO = re.compile(r"conclu[ií]do|^done$|^ok$", re.I)
R_SUCESSO = re.compile(r"candidatura enviada|sua candidatura foi enviada|application (was )?sent|"
                       r"your application was sent|candidatura foi enviada", re.I)
R_LIMITE = re.compile(r"limite (di[aá]rio )?de candidaturas|atingiu o limite|reached the daily|"
                      r"easy apply limit|daily application limit|volte amanh[aã]|try again tomorrow", re.I)
R_BLOQUEIO_URL = re.compile(r"/(checkpoint|authwall|login|uas/login|signup|challenge)(/|\?|$)", re.I)
R_BLOQUEIO_TEXTO = re.compile(r"captcha|verifica[cç][aã]o de seguran[cç]a|security verification|"
                              r"let'?s do a quick security check|vamos fazer uma verifica[cç][aã]o|"
                              r"confirme que voc[eê] [eé] humano|verify you are human", re.I)
R_ANOS = re.compile(r"quantos anos|anos de experi[eê]ncia|tempo de experi[eê]ncia|how many years|years of "
                    r"(work )?experience|years' experience", re.I)
R_ERRO_FORM = re.compile(r"obrigat[oó]ri|required|insira|enter a|selecione uma|select an option|"
                         r"informe|digite|please|corrija|erro", re.I)


class Parar(RuntimeError):
    """Algo exige parar TODA a execução (login/checkpoint/captcha, limite do LinkedIn, PARAR)."""


class DesistirDaVaga(RuntimeError):
    """Esta vaga não pode ser enviada de forma segura; descarta o rascunho e segue para a próxima."""

    def __init__(self, motivo: str, campos: list[str] | None = None):
        super().__init__(motivo)
        self.motivo = motivo
        self.campos = campos or []


@dataclass
class Resultado:
    vaga: str = ""
    resultado: str = ""  # enviada | simulada | desistiu | pulada | erro
    motivo: str = ""
    campos: list[str] = field(default_factory=list)


@dataclass
class Resumo:
    analisadas: int = 0
    elegiveis: int = 0
    enviadas: int = 0
    simuladas: int = 0
    desistencias: int = 0
    puladas: int = 0
    resultados: list[Resultado] = field(default_factory=list)
    encerramento: str = ""

    def texto(self) -> str:
        linhas = ["", "RESUMO LINKEDIN (sem dados pessoais):",
                  f"  vagas analisadas: {self.analisadas}; elegíveis no filtro: {self.elegiveis}",
                  f"  enviadas: {self.enviadas}; simuladas (--sem-enviar): {self.simuladas}; "
                  f"desistências: {self.desistencias}; puladas: {self.puladas}",
                  f"  encerramento: {self.encerramento or 'concluído'}"]
        for r in self.resultados:
            if r.resultado in ("desistiu", "erro") and (r.motivo or r.campos):
                extra = f" (campos: {', '.join(r.campos)})" if r.campos else ""
                linhas.append(f"    - {r.vaga[:50]}: {r.motivo}{extra}")
        return "\n".join(linhas)


# --- JS auxiliar -----------------------------------------------------------------------------

_JS_IDS_VAGAS = r"""
() => {
  const ids = new Set();
  document.querySelectorAll('[data-occludable-job-id], [data-job-id]').forEach(e => {
    const v = e.getAttribute('data-occludable-job-id') || e.getAttribute('data-job-id');
    if (v && /^\d+$/.test(v)) ids.add(v);
  });
  document.querySelectorAll('a[href*="/jobs/view/"]').forEach(a => {
    const m = (a.getAttribute('href') || '').match(/\/jobs\/view\/(?:[^\/?]*-)?(\d{5,})/);
    if (m) ids.add(m[1]);
  });
  return Array.from(ids);
}
"""

_JS_DADOS_VAGA = r"""
() => {
  const txt = (s) => { const e = document.querySelector(s); return e ? (e.innerText || e.textContent || '').trim() : ''; };
  const primeiro = (...sels) => { for (const s of sels) { const t = txt(s); if (t) return t; } return ''; };
  return {
    titulo: primeiro('h1', '.job-details-jobs-unified-top-card__job-title', '[data-test-job-title]'),
    empresa: primeiro('.job-details-jobs-unified-top-card__company-name', '[data-test-company-name]',
                      'a[href*="/company/"]'),
    local: primeiro('.job-details-jobs-unified-top-card__primary-description-container',
                    '.job-details-jobs-unified-top-card__bullet', '[data-test-job-location]'),
    descricao: primeiro('#job-details', '.jobs-description__content', '.jobs-box__html-content',
                        '[data-test-job-description]'),
    titulo_aba: document.title || '',
  };
}
"""

_JS_TEXTO_DIALOGO = r"""
(sel) => { const d = document.querySelector(sel); return d ? (d.innerText || '').replace(/\s+/g, ' ').trim() : ''; }
"""

_JS_ERROS_DIALOGO = r"""
(sel) => {
  const d = document.querySelector(sel); if (!d) return [];
  return Array.from(d.querySelectorAll('[role=alert], .artdeco-inline-feedback--error, [data-test-form-element-error-messages], .fb-dash-form-element__error-field'))
    .map(e => (e.innerText || '').replace(/\s+/g, ' ').trim()).filter(Boolean);
}
"""


# --- núcleo ------------------------------------------------------------------------------------

def valor_anos(respostas: dict[str, Any] | None, texto: str) -> int | None:
    """Anos de experiência para uma pergunta tipo 'Quantos anos de experiência com Python?'.
    Só responde com o que está em respostas.yaml (extras.anos_experiencia: {padrao: N, python: N, ...});
    sem valor definido devolve None (o bot desiste da vaga, não chuta)."""
    tabela = _buscar(respostas or {}, "extras.anos_experiencia")
    if not isinstance(tabela, dict):
        return None
    t = _norm(texto)
    for tec, anos in tabela.items():
        if tec != "padrao" and isinstance(anos, (int, float)) and not isinstance(anos, bool) \
                and re.search(rf"(?<![a-z0-9]){re.escape(_norm(str(tec)))}(?![a-z0-9])", t):
            return int(anos)
    padrao = tabela.get("padrao")
    if isinstance(padrao, (int, float)) and not isinstance(padrao, bool):
        return int(padrao)
    return None


class CopilotoDialogo(Copiloto):
    """Preenche os campos DENTRO do diálogo da Candidatura Simplificada reutilizando o reconhecimento do
    copiloto da Gupy (nunca sobrescreve valor pré-preenchido, nunca toca em consentimento/aberta/login)."""

    def __init__(self, respostas, pdf, **kw):
        super().__init__(respostas, pdf, alvos=ALVOS_LINKEDIN, **kw)

    def campos_do_dialogo(self, pagina) -> list[dict[str, Any]]:
        self.instalar(pagina)
        return pagina.evaluate(_JS_COLETAR, [ATR_ID, SELETOR_DIALOGO])

    def _tratar(self, pagina, c, url):
        # E-mail e telefone da candidatura vêm pré-preenchidos pela conta: nunca tocar (o copiloto os trata
        # como "login"). Pergunta numérica de anos de experiência: só com valor definido no respostas.yaml.
        if not c["feito"] and not c["value"] and not c["editado"] and not c["disabled"] \
                and c["tipo"] in ("text", "number", "tel") and R_ANOS.search(_norm(_texto := cop._texto_campo(c))):
            anos = valor_anos(self.respostas, _texto)
            if anos is None:
                self._pendente(pagina, c, url, "anos de experiência: sem valor em respostas.yaml")
                return "pendente"
            self._com_flag(pagina, lambda: self._loc(pagina, c["id"]).fill(str(anos)))
            self._marcar(pagina, c["id"], "preenchido", "bot-vagas preencheu — confira")
            self.registro._add(self.registro.preenchidos, cop._descricao_campo(c), chave="extras.anos_experiencia",
                               como="texto", url=url)
            return "preenchido"
        return super()._tratar(pagina, c, url)

    def preencher_passo(self, pagina) -> list[str]:
        """Preenche o passo atual. Devolve os nomes dos campos OBRIGATÓRIOS que ficaram sem resposta."""
        url = pagina.url
        faltando: list[str] = []
        for c in self.campos_do_dialogo(pagina):
            try:
                res = self._tratar(pagina, c, url)
            except Exception as e:  # elemento re-renderizou: a validação do passo seguinte acusa
                self.registro.avisos.append(f"falha ao tratar campo '{cop._descricao_campo(c)}': {type(e).__name__}")
                continue
            obrigatorio_vazio = (c["required"] and not c["value"] and not c["checked"] and not c.get("grupo_marcado")
                                 and not c["arquivos"])
            if res == "pendente" and (obrigatorio_vazio or c["tag"] == "textarea" and c["required"]):
                faltando.append(cop._descricao_campo(c))
        return list(dict.fromkeys(faltando))


class SessaoLinkedIn:
    def __init__(self, pagina, cfg: ConfigLinkedIn, respostas, *, banco: Banco, filtros: Filtros, pdf: Path | None,
                 enviar: bool = True, log=print, dormir: Callable[[float], None] = time.sleep,
                 aleatorio: random.Random | None = None, deve_parar: Callable[[], bool] | None = None,
                 log_campos: LogCampos | None = None, notificador=None, hoje_inicio: str | None = None):
        self.pg = pagina
        self.cfg = cfg
        self.respostas = respostas
        self.banco = banco
        self.filtros = filtros
        self.pdf = pdf
        self.enviar = enviar
        self.log = log
        self.dormir = dormir
        self.rnd = aleatorio or random.Random()
        self.deve_parar = deve_parar or (lambda: False)
        self.notificador = notificador
        self.copiloto = CopilotoDialogo(respostas, pdf, log=log, log_campos=log_campos, notificador=notificador)
        self.resumo = Resumo()
        self._hoje_inicio = hoje_inicio

    # -- utilidades ---------------------------------------------------------------------------

    def pausa(self, intervalo: tuple[float, float]) -> None:
        self.dormir(self.rnd.uniform(*intervalo))

    def _checar_parada(self) -> None:
        if self.deve_parar():
            raise Parar("arquivo PARAR")

    def _checar_bloqueio(self) -> None:
        """Login/checkpoint/captcha/limite do LinkedIn: para TUDO (não tenta resolver nem contornar)."""
        url = self.pg.url
        host = (urlsplit(url).hostname or "").lower()
        if not ALVOS_LINKEDIN.host_permitido(host) and not self.copiloto.alvos.permitir_loopback:
            raise Parar(f"a navegação saiu do LinkedIn ({host or 'vazio'})")
        if R_BLOQUEIO_URL.search(urlsplit(url).path or ""):
            self._notificar("login")
            raise Parar("o LinkedIn pediu login/verificação (checkpoint). Resolva você mesmo no navegador")
        try:
            corpo = self.pg.evaluate("() => (document.body ? document.body.innerText : '').slice(0, 4000)")
        except Exception:
            corpo = ""
        if R_BLOQUEIO_TEXTO.search(corpo):
            self._notificar("alarme")
            raise Parar("o LinkedIn mostrou verificação de segurança/captcha. Não contorno: resolva você mesmo")
        if R_LIMITE.search(corpo):
            raise Parar("o LinkedIn informou limite de candidaturas; tente de novo amanhã")

    def _notificar(self, evento: str) -> None:
        if self.notificador is not None:
            self.notificador.notificar(evento)

    def envios_hoje(self) -> int:
        desde = self._hoje_inicio or datetime.now().astimezone().replace(
            hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc).isoformat(timespec="seconds")
        return self.banco.contar_enviadas_desde(FONTE, desde)

    # -- busca --------------------------------------------------------------------------------

    def coletar_ids(self, url: str) -> list[str]:
        self.pg.goto(url, wait_until="domcontentloaded")
        self._checar_bloqueio()
        self.pausa(self.cfg.pausa_acao)
        ids: list[str] = []
        for _ in range(4):  # rolagem curta para carregar mais cartões (a lista é virtualizada)
            ids = list(dict.fromkeys(ids + self.pg.evaluate(_JS_IDS_VAGAS)))
            self.pg.mouse.wheel(0, 1800)
            self.pausa((0.6, 1.4))
        return ids

    def ler_vaga(self, id_vaga: str) -> Vaga | None:
        self.pg.goto(url_vaga(id_vaga, self.cfg.base), wait_until="domcontentloaded")
        self._checar_bloqueio()
        self.pausa(self.cfg.pausa_acao)
        d = self.pg.evaluate(_JS_DADOS_VAGA)
        titulo, empresa = d.get("titulo", ""), d.get("empresa", "")
        if not titulo or not empresa:  # fallback: "Cargo | Empresa | LinkedIn" / "Empresa hiring Cargo"
            partes = [p.strip() for p in (d.get("titulo_aba") or "").split("|")]
            if len(partes) >= 2:
                titulo = titulo or partes[0]
                empresa = empresa or partes[1]
        if not titulo or not empresa:
            return None
        local = re.split(r"\s+[·•]\s+", d.get("local", "") or "")[0].strip()
        return Vaga(id_fonte=id_vaga, fonte=FONTE, empresa=empresa, cargo=titulo, localizacao=local, remoto=None,
                    descricao=html_para_texto(d.get("descricao", "") or ""), url=url_vaga(id_vaga, self.cfg.base))

    # -- candidatura --------------------------------------------------------------------------

    def _botao(self, padrao: re.Pattern, escopo=None, papel: str = "button"):
        base = escopo or self.pg
        return base.get_by_role(papel, name=padrao).first

    def _clicar(self, loc) -> None:
        self.pausa(self.cfg.pausa_acao)
        loc.click(timeout=8000)

    def abrir_formulario(self) -> None:
        """Abre o diálogo da Candidatura Simplificada. Desiste se a vaga não a tem ou já foi enviada."""
        corpo = self.pg.evaluate("() => (document.body ? document.body.innerText : '').slice(0, 6000)")
        botao = self._botao(R_SIMPLIFICADA)
        if botao.count() == 0:
            botao = self._botao(R_SIMPLIFICADA, papel="link")
        if botao.count() == 0:
            if R_JA_CANDIDATOU.search(corpo):
                raise DesistirDaVaga("você já se candidatou a esta vaga")
            raise DesistirDaVaga("sem Candidatura Simplificada (candidatura no site da empresa: faça à mão)")
        self._clicar(botao)
        try:
            self.pg.locator(SELETOR_DIALOGO).first.wait_for(state="visible", timeout=10000)
        except Exception:
            raise DesistirDaVaga("o formulário da Candidatura Simplificada não abriu") from None

    def fechar_formulario(self) -> None:
        """Fecha e DESCARTA o rascunho (nada é enviado)."""
        try:
            d = self.pg.locator(SELETOR_DIALOGO).first
            if d.count() == 0:
                return
            fechar = self._botao(R_DISPENSAR, escopo=d)
            if fechar.count():
                fechar.click(timeout=5000)
            else:
                self.pg.keyboard.press("Escape")
            descartar = self._botao(R_DESCARTAR)
            if descartar.count():
                descartar.click(timeout=5000)
        except Exception:
            pass

    def percorrer_formulario(self) -> str:
        """Preenche e avança. Devolve 'enviada', 'simulada' ou levanta DesistirDaVaga/Parar."""
        for passo in range(MAX_PASSOS_FORMULARIO):
            self._checar_parada()
            self._checar_bloqueio()
            dialogo = self.pg.locator(SELETOR_DIALOGO).first
            if dialogo.count() == 0:
                raise DesistirDaVaga("o formulário fechou sozinho antes do envio")
            texto = self.pg.evaluate(_JS_TEXTO_DIALOGO, SELETOR_DIALOGO)
            if R_SUCESSO.search(texto):
                return "enviada"
            faltando = self.copiloto.preencher_passo(self.pg)
            if faltando:
                raise DesistirDaVaga("pergunta(s) obrigatória(s) sem resposta segura", faltando)
            self.pausa(self.cfg.pausa_acao)
            enviar = self._botao(R_ENVIAR, escopo=dialogo)
            if enviar.count():
                if not self.enviar:
                    return "simulada"
                self._clicar(enviar)
                self.pausa(self.cfg.pausa_acao)
                texto = self.pg.evaluate(_JS_TEXTO_DIALOGO, SELETOR_DIALOGO) or ""
                corpo = self.pg.evaluate("() => (document.body ? document.body.innerText : '').slice(0, 4000)")
                if R_SUCESSO.search(texto) or R_SUCESSO.search(corpo):
                    concl = self._botao(R_CONCLUIDO)
                    if concl.count():
                        try:
                            concl.click(timeout=3000)
                        except Exception:
                            pass
                    return "enviada"
                raise DesistirDaVaga("cliquei em enviar mas o LinkedIn não confirmou o envio (confira à mão)")
            avancar = self._botao(R_REVISAR, escopo=dialogo)
            if avancar.count() == 0:
                avancar = self._botao(R_PROXIMO, escopo=dialogo)
            if avancar.count() == 0:
                raise DesistirDaVaga("não achei o botão de avançar/enviar (o layout do LinkedIn mudou?)")
            self._clicar(avancar)
            self.pausa((0.8, 1.6))
            erros = [e for e in self.pg.evaluate(_JS_ERROS_DIALOGO, SELETOR_DIALOGO) if R_ERRO_FORM.search(e)]
            if erros:
                raise DesistirDaVaga("o LinkedIn recusou o passo (campo inválido/obrigatório)",
                                     self._campos_com_erro())
        raise DesistirDaVaga("formulário longo demais (mais de %d passos)" % MAX_PASSOS_FORMULARIO)

    def _campos_com_erro(self) -> list[str]:
        try:
            nomes = self.pg.evaluate(
                "(sel) => Array.from(document.querySelectorAll(sel + ' [aria-invalid=true]')).map(e => "
                "(e.getAttribute('aria-label') || (e.labels && e.labels[0] ? e.labels[0].innerText : '') || e.name || e.id || 'campo')"
                ".replace(/\\s+/g, ' ').trim().slice(0, 60))", SELETOR_DIALOGO)
            return list(dict.fromkeys(n for n in nomes if n))
        except Exception:
            return []

    def candidatar(self, vaga: Vaga) -> Resultado:
        rotulo = f"{vaga.empresa} / {vaga.cargo}"
        try:
            self.abrir_formulario()
            res = self.percorrer_formulario()
            if res == "simulada":
                self.fechar_formulario()
            return Resultado(rotulo, res)
        except DesistirDaVaga as e:
            self.fechar_formulario()
            return Resultado(rotulo, "desistiu", e.motivo, e.campos)
        except Parar:
            self.fechar_formulario()
            raise
        except Exception as e:  # layout inesperado, timeout, aba fechada...
            if isinstance(e, KeyboardInterrupt):
                raise
            self.fechar_formulario()
            return Resultado(rotulo, "erro", f"{type(e).__name__} ao operar o formulário")

    # -- registro no banco -------------------------------------------------------------------------

    def _gravar(self, vaga: Vaga, r: Resultado) -> None:
        self.banco.salvar_vagas([vaga])
        chave = chave_vaga(vaga.empresa, vaga.cargo, vaga.url)
        row = self.banco.buscar_por_chave(chave)
        try:
            detalhes = json.loads(row["detalhes"]) if row and row["detalhes"] else {}
        except ValueError:
            detalhes = {}
        if not isinstance(detalhes, dict):
            detalhes = {}
        detalhes["linkedin"] = {"quando": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                "resultado": r.resultado, "motivo": r.motivo, "campos": r.campos}
        if r.resultado == "enviada":
            self.banco.atualizar_status(chave, "enviada", detalhes=json.dumps(detalhes, ensure_ascii=False))
        else:
            # falha/simulação: a vaga continua como estava (nova/aprovada...), só anota a tentativa
            atual = row["status"] if row else "nova"
            self.banco.atualizar_status(chave, atual, detalhes=json.dumps(detalhes, ensure_ascii=False),
                                        motivo=(r.motivo or None) if r.resultado in ("desistiu", "erro") else None)

    def ja_tratada(self, vaga: Vaga) -> bool:
        row = self.banco.buscar_por_chave(chave_vaga(vaga.empresa, vaga.cargo, vaga.url)) \
            or self.banco.buscar_por_url(vaga.url)
        if row is None:
            return False
        if row["status"] in ("enviada", "em_candidatura", "desistiu"):
            return True
        try:
            tentativa = (json.loads(row["detalhes"]) if row["detalhes"] else {}).get("linkedin") or {}
        except ValueError:
            tentativa = {}
        return tentativa.get("resultado") in ("desistiu", "erro", "enviada")  # já tentada: não repete sozinha

    # -- laço principal ----------------------------------------------------------------------------

    def executar(self, maximo: int | None = None) -> Resumo:
        limite_dia = self.cfg.max_envios_por_dia
        restante = max(0, limite_dia - self.envios_hoje()) if self.enviar else limite_dia
        if maximo is not None:
            restante = min(restante, maximo)
        r = self.resumo
        if restante <= 0:
            r.encerramento = f"limite diário atingido ({limite_dia} envios hoje)"
            return r
        falhas_seguidas = 0
        vistos: set[str] = set()
        try:
            for termo, local in self.cfg.buscas:
                self._checar_parada()
                ids = self.coletar_ids(url_busca(termo, local, self.cfg))
                self.log(f"busca {termo!r} em {local!r}: {len(ids)} vaga(s)")
                for id_vaga in ids:
                    if id_vaga in vistos:
                        continue
                    vistos.add(id_vaga)
                    self._checar_parada()
                    if r.analisadas >= self.cfg.max_vagas_por_execucao:
                        r.encerramento = "limite de vagas analisadas por execução"
                        return r
                    r.analisadas += 1
                    vaga = self.ler_vaga(id_vaga)
                    if vaga is None:
                        r.puladas += 1
                        continue
                    if self.ja_tratada(vaga):
                        r.puladas += 1
                        continue
                    filtro = avaliar_palavras_chave(vaga, self.filtros)
                    if not filtro.passou:
                        r.puladas += 1
                        self.banco.salvar_vagas([vaga])
                        continue
                    r.elegiveis += 1
                    self.log(f"candidatando: {vaga.empresa} / {vaga.cargo}")
                    res = self.candidatar(vaga)
                    r.resultados.append(res)
                    self._gravar(vaga, res)
                    if res.resultado == "enviada":
                        r.enviadas += 1
                        falhas_seguidas = 0
                    elif res.resultado == "simulada":
                        r.simuladas += 1
                        falhas_seguidas = 0
                    else:
                        r.desistencias += 1
                        falhas_seguidas += 1
                        self.log(f"  desisti: {res.motivo}" + (f" ({', '.join(res.campos)})" if res.campos else ""))
                        if falhas_seguidas >= self.cfg.falhas_seguidas_para_parar:
                            r.encerramento = (f"{falhas_seguidas} falhas seguidas: o layout do LinkedIn pode ter "
                                              "mudado; parei para não insistir")
                            return r
                    if r.enviadas + r.simuladas >= restante:
                        r.encerramento = ("limite do dia/execução atingido" if self.enviar else
                                          "limite de simulações atingido")
                        return r
                    self.pausa(self.cfg.pausa_vaga)
        except Parar as e:
            r.encerramento = f"parado: {e}"
        return r


# --- navegador visível (só na CLI) ---------------------------------------------------------------

def login_linkedin(pasta_perfil: Path | str = cop.PASTA_PERFIL_PADRAO, *, url: str = URL_LOGIN, log=print,
                   headless: bool = False, max_segundos: float | None = None,
                   deve_parar: Callable[[], bool] | None = None) -> int:
    """Abre o login do LinkedIn para VOCÊ entrar (senha/2FA ficam só com você). Mesmo perfil do navegador."""
    return cop.login_gupy(pasta_perfil, url=url, log=log, headless=headless, max_segundos=max_segundos,
                          deve_parar=deve_parar)


def aplicar(cfg: ConfigLinkedIn, respostas, *, banco: Banco, filtros: Filtros, pdf: Path | None,
            pasta_perfil: Path | str = cop.PASTA_PERFIL_PADRAO, headless: bool = False, enviar: bool = True,
            maximo: int | None = None, log=print, deve_parar: Callable[[], bool] | None = None,
            log_campos: LogCampos | None = None, notificador=None, dormir: Callable[[float], None] | None = None,
            permitir_loopback: bool = False, url_inicial: str | None = None) -> Resumo:
    """Abre o Chromium (perfil persistente, onde você já fez login) e roda a sessão. Nunca propaga
    Ctrl+C/fechamento: devolve o resumo com `encerramento`. Só levanta PerfilEmUso."""
    from playwright.sync_api import Error as ErroPW, sync_playwright

    pasta = preparar_pasta_perfil(pasta_perfil)
    if perfil_em_uso(pasta):
        raise PerfilEmUso()
    log(cop.AVISO_PERFIL.format(pasta=pasta))
    log("LinkedIn: o bot vai CLICAR e ENVIAR candidaturas (decisão sua; risco para a conta). "
        "PARAR: `bot-vagas parar`. Feche a janela ou Ctrl+C para terminar."
        + ("" if enviar else " MODO SIMULAÇÃO: nada será enviado."))
    pw = ctx = None
    seguro = True
    sessao: SessaoLinkedIn | None = None
    resumo = Resumo()
    with _CapturaSinais() as sinais:
        try:
            pw = sync_playwright().start()
            ctx = abrir_contexto_persistente(pw, pasta, headless=headless)
            pagina = ctx.pages[0] if ctx.pages else ctx.new_page()
            parar = lambda: bool(sinais.recebido) or (deve_parar() if deve_parar else False)  # noqa: E731
            sessao = SessaoLinkedIn(pagina, cfg, respostas, banco=banco, filtros=filtros, pdf=pdf, enviar=enviar,
                                    log=log, deve_parar=parar, log_campos=log_campos, notificador=notificador,
                                    dormir=dormir or time.sleep)
            if permitir_loopback:
                sessao.copiloto.alvos = Alvos(hosts=HOSTS_LINKEDIN, permitir_loopback=True)
            resumo = sessao.resumo
            sessao.executar(maximo)
        except PerfilEmUso:
            raise
        except (KeyboardInterrupt, cop.SinalDeTermino):
            seguro = False
            sinais.recebido = sinais.recebido or "Ctrl+C"
        except ErroPW as e:
            fechado = ctx is None or not ctx.pages or "closed" in str(e).lower()
            resumo.encerramento = "janela fechada" if fechado else f"erro do navegador ({type(e).__name__})"
        except Exception as e:
            resumo.encerramento = f"erro inesperado ({type(e).__name__})"
        finally:
            _encerrar_navegador(ctx, pw, seguro=seguro)
    if sinais.recebido:
        resumo.encerramento = f"interrompido ({sinais.recebido})"
    return resumo
