"""Carrega UMA página de vaga (Etapa 4) e extrai título, empresa, localização e descrição.

Regras:
- só http/https; recusa localhost, loopback, IPs privados/link-local/reservados e hostnames que
  resolvam para eles (SSRF). Redirecionamentos passam pela mesma checagem (handler de rota);
- Playwright headless com User-Agent genérico, sem cookies/login, timeout, sem seguir para outras
  páginas (toda navegação fora da URL inicial e seus redirecionamentos é abortada);
- nunca tenta burlar muro de login ou captcha: detecta e para com mensagem clara;
- `fetch` é injetável nos testes (função url -> (url_final, html)); o Playwright real só roda na CLI.
"""

from __future__ import annotations

import html as _html
import ipaddress
import json
import re
import socket
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any, Callable
from urllib.parse import urlsplit

from .fontes.texto import html_para_texto

USER_AGENT = "bot-vagas/0.1"
TIMEOUT_MS = 25_000
MINIMO_TEXTO = 200  # menos que isso = página vazia/bloqueada
MAX_REDIRECIONAMENTOS = 5
_ARGS_NAVEGADOR: tuple[str, ...] = ()  # só para smoke local (ex.: --host-resolver-rules); vazio em produção

Fetch = Callable[[str], tuple[str, str]]
"""(url) -> (url_final, html). O real é `fetch_playwright`."""

Resolver = Callable[[str], list[str]]
"""(hostname) -> lista de IPs. Injetável nos testes (nunca faz DNS real lá)."""


class ErroPagina(RuntimeError):
    """Qualquer motivo para não continuar (URL recusada, bloqueio, conteúdo vazio)."""


class URLRecusada(ErroPagina):
    pass


class PaginaBloqueada(ErroPagina):
    """Login, captcha ou conteúdo insuficiente. A mensagem sugere --arquivo."""


@dataclass
class PaginaExtraida:
    url: str
    url_final: str
    titulo: str = ""
    empresa: str = ""
    localizacao: str = ""
    descricao: str = ""
    avisos: list[str] = field(default_factory=list)


# --- SSRF ---------------------------------------------------------------------

_HOSTS_PROIBIDOS = {"localhost", "localhost.localdomain", "ip6-localhost", "ip6-loopback"}


def _resolver_real(host: str) -> list[str]:
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror as e:
        raise URLRecusada(f"não foi possível resolver o host '{host}': {e}") from None
    return sorted({i[4][0] for i in infos})


def _ip_proibido(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip.split("%")[0])
    except ValueError:
        return True
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
        addr = addr.ipv4_mapped
    return (
        addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_multicast
        or addr.is_reserved or addr.is_unspecified or not addr.is_global
    )


def validar_url(url: str, resolver: Resolver | None = None) -> str:
    """Devolve a URL limpa ou levanta URLRecusada. Resolve o host e confere todos os IPs."""
    url = (url or "").strip()
    try:
        p = urlsplit(url)
    except ValueError:
        raise URLRecusada(f"URL malformada: {url!r}") from None
    if p.scheme.lower() not in ("http", "https"):
        raise URLRecusada(f"só http/https são aceitos (recebido: {p.scheme or 'sem esquema'!r})")
    if p.username or p.password:
        raise URLRecusada("URL com usuário/senha embutidos não é aceita")
    host = (p.hostname or "").strip().lower().rstrip(".")
    if not host:
        raise URLRecusada("URL sem host")
    if host in _HOSTS_PROIBIDOS or host.endswith(".localhost") or host.endswith(".local"):
        raise URLRecusada(f"host local recusado: {host}")
    try:
        ipaddress.ip_address(host)
        ips = [host]  # IP literal
    except ValueError:
        ips = (resolver or _resolver_real)(host)
    if not ips:
        raise URLRecusada(f"host '{host}' não resolve para nenhum IP")
    for ip in ips:
        if _ip_proibido(ip):
            raise URLRecusada(f"host '{host}' aponta para endereço privado/local ({ip}); recusado por segurança")
    return url


# --- extração -----------------------------------------------------------------

class _Coletor(HTMLParser):
    """Separa <title>, metas, JSON-LD, h1 e o corpo sem script/style/nav."""

    _IGNORAR = {"script", "style", "noscript", "svg", "template", "head", "iframe"}
    _BLOCO = {"p", "br", "li", "div", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "tr", "section", "article", "header", "footer", "nav", "table"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.metas: dict[str, str] = {}
        self.jsonld: list[str] = []
        self.h1: list[str] = []
        self.corpo: list[str] = []
        self._pilha: list[str] = []
        self._em_title = False
        self._em_h1 = False
        self._em_jsonld = False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "meta":
            chave = (a.get("property") or a.get("name") or "").lower()
            if chave and a.get("content"):
                self.metas.setdefault(chave, a["content"].strip())
            return
        if tag == "script" and (a.get("type") or "").lower() == "application/ld+json":
            self._em_jsonld = True
            self.jsonld.append("")
        if tag in self._IGNORAR:
            self._pilha.append(tag)
        elif tag == "title":
            self._em_title = True
        elif tag == "h1":
            self._em_h1 = True
            self.h1.append("")
        if tag in self._BLOCO:
            self.corpo.append("\n")

    def handle_endtag(self, tag):
        if tag in self._IGNORAR and self._pilha and self._pilha[-1] == tag:
            self._pilha.pop()
        if tag == "script":
            self._em_jsonld = False
        if tag == "title":
            self._em_title = False
        if tag == "h1":
            self._em_h1 = False
        if tag in self._BLOCO:
            self.corpo.append("\n")

    def handle_data(self, data):
        if self._em_jsonld:
            self.jsonld[-1] += data
            return
        if self._em_title:
            self.title += data
        if self._pilha:
            return
        if self._em_h1 and self.h1:
            self.h1[-1] += data
        self.corpo.append(data)


def _limpar(texto: str) -> str:
    texto = re.sub(r"[ \t\r\f\v]+", " ", texto)
    texto = re.sub(r"\n\s*\n+", "\n", texto)
    return texto.strip()


def _jobposting(blocos: list[str]) -> dict[str, Any] | None:
    for bruto in blocos:
        try:
            dados = json.loads(bruto.strip())
        except (ValueError, TypeError):
            continue
        candidatos = dados if isinstance(dados, list) else [dados]
        for d in candidatos:
            if not isinstance(d, dict):
                continue
            if isinstance(d.get("@graph"), list):
                candidatos.extend(x for x in d["@graph"] if isinstance(x, dict))
            tipo = d.get("@type")
            tipos = tipo if isinstance(tipo, list) else [tipo]
            if "JobPosting" in tipos:
                return d
    return None


def _str(v: Any) -> str:
    return v.strip() if isinstance(v, str) else ""


def _local_jobposting(d: dict[str, Any]) -> str:
    partes: list[str] = []
    loc = d.get("jobLocation")
    locs = loc if isinstance(loc, list) else [loc]
    for l in locs:
        if not isinstance(l, dict):
            continue
        end = l.get("address") if isinstance(l.get("address"), dict) else l
        for k in ("addressLocality", "addressRegion", "addressCountry"):
            v = end.get(k)
            if isinstance(v, dict):
                v = v.get("name")
            if _str(v):
                partes.append(_str(v))
        break
    if _str(d.get("jobLocationType")) == "TELECOMMUTE":
        partes.append("Remote")
    return ", ".join(dict.fromkeys(partes))


_PADROES_BLOQUEIO = (
    r"linkedin\.com/(authwall|checkpoint|login|uas/login)",
    r"/login\b", r"/signin\b", r"/sign-in\b", r"/entrar\b", r"/auth\b",
)
_TEXTOS_BLOQUEIO = (
    "captcha", "verify you are human", "verifique que você é humano", "are you a robot",
    "access denied", "attention required", "checking your browser", "just a moment",
    "please log in", "sign in to continue", "faça login para continuar", "entrar para continuar",
    "join now to see", "cadastre-se para ver",
)


def detectar_bloqueio(url_final: str, texto: str) -> str | None:
    """Devolve a razão do bloqueio (login/captcha/vazio) ou None."""
    u = url_final.lower()
    for p in _PADROES_BLOQUEIO:
        if re.search(p, u):
            return f"a página redirecionou para uma tela de login ({url_final})"
    t = texto.lower()
    curto = len(texto) < MINIMO_TEXTO * 4
    for marca in _TEXTOS_BLOQUEIO:
        if marca in t and curto:
            return f"a página parece exigir login ou captcha (\"{marca}\")"
    if len(texto) < MINIMO_TEXTO:
        return f"a página tem conteúdo de texto insuficiente ({len(texto)} caracteres)"
    return None


def extrair(url: str, url_final: str, html: str) -> PaginaExtraida:
    """Extrai os campos de um HTML já baixado. Levanta PaginaBloqueada se não dá para usar."""
    col = _Coletor()
    try:
        col.feed(html or "")
        col.close()
    except Exception:
        pass
    corpo = _limpar(_html.unescape("".join(col.corpo)))
    pag = PaginaExtraida(url=url, url_final=url_final)

    jp = _jobposting(col.jsonld)
    if jp:
        pag.titulo = _str(jp.get("title"))
        org = jp.get("hiringOrganization")
        pag.empresa = _str(org.get("name")) if isinstance(org, dict) else _str(org)
        pag.localizacao = _local_jobposting(jp)
        desc = _str(jp.get("description"))
        if desc:
            pag.descricao = _limpar(html_para_texto(desc))
        pag.avisos.append("dados estruturados (JSON-LD JobPosting) encontrados na página")

    titulo_tag = _limpar(col.title)
    og_title = col.metas.get("og:title", "")
    if not pag.titulo:
        pag.titulo = _limpar(col.h1[0]) if col.h1 and col.h1[0].strip() else (og_title or titulo_tag)
    if not pag.empresa:
        pag.empresa = col.metas.get("og:site_name", "") or col.metas.get("author", "")
    if not pag.empresa and titulo_tag:
        # "Cargo - Empresa" / "Cargo | Empresa" no <title>
        m = re.split(r"\s+[-|–—·@]\s+", titulo_tag)
        if len(m) >= 2:
            pag.empresa = m[-1].strip()
    if not pag.descricao:
        pag.descricao = corpo
    pag.titulo = re.sub(r"\s+", " ", pag.titulo).strip()[:200]
    pag.empresa = re.sub(r"\s+", " ", pag.empresa).strip()[:120]

    motivo = detectar_bloqueio(url_final, pag.descricao)
    if motivo:
        raise PaginaBloqueada(
            f"não consegui ler a vaga: {motivo}. O bot não tenta contornar login nem captcha. "
            "Copie o texto da vaga para um arquivo e use: bot-vagas preparar --arquivo vaga.txt "
            f"--empresa \"...\" --cargo \"...\" --url {url}"
        )
    return pag


# --- Playwright (só na CLI) ----------------------------------------------------

def fetch_playwright(url: str, *, resolver: Resolver | None = None, timeout_ms: int = TIMEOUT_MS) -> tuple[str, str]:
    """Abre a URL em um Chromium headless limpo. Aborta navegações para hosts privados
    (inclusive redirecionamentos) e qualquer navegação de nível superior que saia da cadeia inicial."""
    from playwright.sync_api import Error as ErroPW, TimeoutError as TimeoutPW, sync_playwright

    recusas: list[str] = []
    estado = {"url_final": url, "navegou": False}

    def rota(route, request):
        try:
            if request.is_navigation_request() and request.frame == request.frame.page.main_frame:
                if estado["navegou"]:
                    # a página tentou ir para OUTRA página (meta refresh, JS): não seguimos
                    recusas.append(f"navegação extra bloqueada: {request.url}")
                    route.abort()
                    return
                estado["navegou"] = True
                # Redirecionamentos seguidos MANUALMENTE: o Chromium não passa redirecionamentos
                # pelo route, então cada Location é validado ANTES de ser requisitado.
                atual = validar_url(request.url, resolver)
                resposta = None
                for _ in range(MAX_REDIRECIONAMENTOS + 1):
                    resposta = route.fetch(url=atual, max_redirects=0)
                    destino = resposta.headers.get("location")
                    if 300 <= resposta.status < 400 and destino:
                        from urllib.parse import urljoin

                        atual = validar_url(urljoin(atual, destino), resolver)
                        continue
                    break
                else:
                    raise ErroPagina(f"mais de {MAX_REDIRECIONAMENTOS} redirecionamentos")
                estado["url_final"] = atual
                route.fulfill(response=resposta)
                return
            if request.resource_type in ("image", "media", "font", "websocket", "manifest"):
                route.abort()
                return
            validar_url(request.url, resolver)
        except ErroPagina as e:
            recusas.append(str(e))
            route.abort()
            return
        except ErroPW as e:  # falha de rede no fetch manual
            recusas.append(f"falha ao carregar: {str(e).splitlines()[0]}")
            route.abort()
            return
        route.continue_()

    with sync_playwright() as p:
        navegador = p.chromium.launch(args=list(_ARGS_NAVEGADOR))
        try:
            ctx = navegador.new_context(user_agent=USER_AGENT, locale="pt-BR", java_script_enabled=True,
                                        viewport={"width": 1280, "height": 900})
            pagina = ctx.new_page()
            pagina.set_default_timeout(timeout_ms)
            pagina.route("**/*", rota)
            try:
                pagina.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                try:
                    pagina.wait_for_load_state("networkidle", timeout=min(timeout_ms, 8_000))
                except TimeoutPW:
                    pass
            except TimeoutPW:
                raise ErroPagina(f"tempo esgotado ({timeout_ms / 1000:.0f}s) ao carregar {url}") from None
            except ErroPW as e:
                if recusas:
                    raise URLRecusada(f"navegação abortada: {recusas[0]}") from None
                raise ErroPagina(f"falha ao carregar {url}: {str(e).splitlines()[0]}") from None
            return estado["url_final"], pagina.content()
        finally:
            navegador.close()


def carregar_pagina(url: str, *, fetch: Fetch | None = None, resolver: Resolver | None = None) -> PaginaExtraida:
    """Valida a URL, baixa UMA página e extrai os campos. `fetch` falso nos testes."""
    url = validar_url(url, resolver)
    if fetch is None:
        url_final, html = fetch_playwright(url, resolver=resolver)
    else:
        url_final, html = fetch(url)
    # a URL final (após redirecionamentos) passa pela mesma checagem
    validar_url(url_final, resolver)
    return extrair(url, url_final, html)
