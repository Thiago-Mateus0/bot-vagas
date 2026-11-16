"""Gupy — leitura da página PÚBLICA de busca do portal (sem login, sem rota interna).

Como funciona (verificado em 2026-11-13 por Thiago, com requisições reais):
  GET https://portal.gupy.io/job-search/term=<termo url-encoded>[&page=N]
devolve o HTML que um navegador carrega. Dentro dele há
  <script id="__NEXT_DATA__" type="application/json">{...}</script>
com props.pageProps.initialJobList = {"data": [...], "pagination": {"total", "limit", "offset"}}.
Cada item de `data`: id, name, careerPageName, careerPageUrl, jobUrl, city, state,
workplaceType (remote | hybrid | on-site), publishedDate, applicationDeadline, description, type...
A busca é literal pelo termo no título. Paginação: `&page=2`, `&page=3`... (`&offset=` não funciona).

RISCO: essa estrutura NÃO é documentada pela Gupy e pode mudar a qualquer momento. Por isso
qualquer desvio (sem __NEXT_DATA__, JSON quebrado, chaves faltando, HTTP != 200, página de
captcha/bloqueio) dispara um ALARME: a fonte para aquele termo (ou todas as requisições, em
429/403/captcha), registra uma mensagem clara em `self.alarmes` e NÃO tenta contornar nada.
O comando `buscar` termina com código de saída 1 quando há alarme (as outras fontes seguem).

Limites de gentileza (configuráveis em fontes.yaml, seção `gupy`):
  - teto de páginas por termo (padrão 5), teto total de requisições por execução (padrão 40);
  - pausa mínima de 2 s entre requisições (o código impõe o mínimo), timeout 20 s;
  - só https e só o host portal.gupy.io (redirecionamento para outro host é recusado).
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from typing import Any, Callable
from urllib.parse import quote, urlsplit

from . import Vaga
from .http import ClienteHTTP, ErroHTTP, RespostaHTML
from .texto import html_para_texto

HOST = "portal.gupy.io"
URL_BASE = f"https://{HOST}/job-search/term="
PAUSA_MINIMA_S = 2.0
MAX_PAGINAS_PADRAO = 5
MAX_REQUISICOES_PADRAO = 40
TIMEOUT_S = 20

MSG_ALARME = "Gupy: formato da página mudou ou acesso bloqueado — parei; nada foi contornado"

_RE_NEXT_DATA = re.compile(
    r'<script[^>]*\bid="__NEXT_DATA__"[^>]*>(.*?)</script>', re.DOTALL | re.IGNORECASE
)
# Sinais de página de bloqueio/captcha (comparação em minúsculas). Só usados quando NÃO há
# __NEXT_DATA__; nunca tentamos resolver nada, apenas paramos.
_SINAIS_BLOQUEIO = ("captcha", "cf-chl", "challenge-platform", "access denied", "acesso negado",
                    "attention required", "verify you are human", "unusual traffic")
_STATUS_PARA_TUDO = {403, 429}


class AlarmeGupy(RuntimeError):
    """Formato inesperado ou bloqueio. `parar_tudo=True` encerra todas as requisições à Gupy."""

    def __init__(self, detalhe: str, parar_tudo: bool = False):
        super().__init__(f"{MSG_ALARME} ({detalhe})")
        self.detalhe = detalhe
        self.parar_tudo = parar_tudo


class Gupy:
    nome = "gupy"
    implementada = True

    def __init__(
        self,
        termos: list[str] | tuple[str, ...] | str,
        *,
        max_paginas_por_termo: int = MAX_PAGINAS_PADRAO,
        max_requisicoes: int = MAX_REQUISICOES_PADRAO,
        pausa_segundos: float = PAUSA_MINIMA_S,
        dormir: Callable[[float], None] = time.sleep,
        relogio: Callable[[], float] = time.monotonic,
        agora: Callable[[], datetime] | None = None,
    ):
        if isinstance(termos, str):
            termos = [termos]
        self.termos = [str(t).strip() for t in termos if str(t).strip()]
        self.max_paginas_por_termo = max(1, int(max_paginas_por_termo))
        self.max_requisicoes = max(1, int(max_requisicoes))
        self.pausa_segundos = max(PAUSA_MINIMA_S, float(pausa_segundos))  # mínimo imposto aqui
        self._dormir = dormir
        self._relogio = relogio
        self._agora = agora or (lambda: datetime.now(timezone.utc))
        self.alarmes: list[str] = []
        self.requisicoes = 0
        self._ultima: float | None = None

    # rótulo usado nos logs do pipeline
    @property
    def token(self) -> str:
        return f"{len(self.termos)} termos"

    @staticmethod
    def url(termo: str, pagina: int = 1) -> str:
        u = URL_BASE + quote(termo, safe="")
        if pagina > 1:
            u += f"&page={pagina}"
        return u

    # --- rede -----------------------------------------------------------------

    def _pausar(self) -> None:
        if self._ultima is not None:
            espera = self.pausa_segundos - (self._relogio() - self._ultima)
            if espera > 0:
                self._dormir(espera)

    def _baixar(self, http: ClienteHTTP, url: str) -> RespostaHTML:
        if self.requisicoes >= self.max_requisicoes:
            raise AlarmeGupy(f"teto de {self.max_requisicoes} requisições atingido", parar_tudo=True)
        self._pausar()
        self.requisicoes += 1
        try:
            resp = http.get_html(url, hosts_permitidos=(HOST,))
        finally:
            self._ultima = self._relogio()
        host_final = (urlsplit(resp.url_final).hostname or "").lower()
        if not resp.url_final.startswith("https://") or host_final != HOST:
            raise ErroHTTP(f"Gupy: redirecionamento recusado para {resp.url_final}")
        if resp.status in _STATUS_PARA_TUDO:
            raise AlarmeGupy(f"HTTP {resp.status} em {url}", parar_tudo=True)
        if resp.status != 200:
            raise AlarmeGupy(f"HTTP {resp.status} em {url}")
        return resp

    # --- busca ----------------------------------------------------------------

    def buscar(self, http: ClienteHTTP) -> list[Vaga]:
        self.alarmes = []
        self.requisicoes = 0
        vistos: set[str] = set()
        vagas: list[Vaga] = []
        parar_tudo = False
        for termo in self.termos:
            if parar_tudo:
                break
            pagina = 1
            while pagina <= self.max_paginas_por_termo:
                url = self.url(termo, pagina)
                try:
                    resp = self._baixar(http, url)
                    lista, paginacao = extrair_lista(resp.texto)
                except AlarmeGupy as e:
                    self.alarmes.append(f"[termo {termo!r}, página {pagina}] {e}")
                    if e.parar_tudo:
                        parar_tudo = True
                    break
                for item in lista:
                    v = converter_item(item, agora=self._agora())
                    if v is not None and v.id_fonte not in vistos:
                        vistos.add(v.id_fonte)
                        vagas.append(v)
                if not lista or paginacao["offset"] + paginacao["limit"] >= paginacao["total"]:
                    break
                pagina += 1
        return vagas


# --- parsing (puro, sem rede) ---------------------------------------------------


def extrair_next_data(html: str) -> dict:
    """Devolve o JSON de __NEXT_DATA__ ou levanta AlarmeGupy (com parar_tudo se parecer captcha)."""
    m = _RE_NEXT_DATA.search(html or "")
    if not m:
        baixo = (html or "").lower()
        if any(s in baixo for s in _SINAIS_BLOQUEIO):
            raise AlarmeGupy("página de bloqueio/captcha", parar_tudo=True)
        raise AlarmeGupy("HTML sem __NEXT_DATA__")
    try:
        dados = json.loads(m.group(1))
    except json.JSONDecodeError as e:
        raise AlarmeGupy(f"__NEXT_DATA__ não é JSON válido: {e}") from None
    if not isinstance(dados, dict):
        raise AlarmeGupy("__NEXT_DATA__ não é um objeto JSON")
    return dados


def extrair_lista(html: str) -> tuple[list[dict], dict[str, int]]:
    """Devolve (data, pagination) de props.pageProps.initialJobList ou levanta AlarmeGupy."""
    dados = extrair_next_data(html)
    lista_job = (((dados.get("props") or {}).get("pageProps") or {}).get("initialJobList"))
    if not isinstance(lista_job, dict):
        raise AlarmeGupy("props.pageProps.initialJobList ausente")
    data = lista_job.get("data")
    pag = lista_job.get("pagination")
    if not isinstance(data, list) or not isinstance(pag, dict):
        raise AlarmeGupy("initialJobList sem `data` (lista) ou `pagination` (objeto)")
    try:
        paginacao = {k: int(pag[k]) for k in ("total", "limit", "offset")}
    except (KeyError, TypeError, ValueError):
        raise AlarmeGupy("pagination sem total/limit/offset numéricos") from None
    if paginacao["limit"] <= 0:
        raise AlarmeGupy("pagination.limit inválido")
    return [x for x in data if isinstance(x, dict)], paginacao


def _data(valor: Any) -> datetime | None:
    if not isinstance(valor, str) or not valor:
        return None
    try:
        d = datetime.fromisoformat(valor.replace("Z", "+00:00"))
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d


def converter_item(item: dict, *, agora: datetime | None = None) -> Vaga | None:
    """Um item de `data` -> Vaga. Devolve None se faltar campo essencial ou o prazo já venceu.
    `description` é tratada como dado não confiável: vira texto limpo, só isso."""
    if not isinstance(item, dict):
        return None
    id_ = item.get("id")
    nome = item.get("name")
    url = item.get("jobUrl")
    empresa = item.get("careerPageName")
    if id_ is None or not nome or not isinstance(url, str) or not url.startswith("https://") or not empresa:
        return None
    agora = agora or datetime.now(timezone.utc)
    prazo = _data(item.get("applicationDeadline"))
    if prazo is not None and prazo < agora:
        return None

    tipo = str(item.get("workplaceType") or "").lower().strip()
    cidade = str(item.get("city") or "").strip()
    estado = str(item.get("state") or "").strip()
    lugar = ", ".join(x for x in (cidade, estado) if x)
    if tipo == "remote":
        remoto: bool | None = True
        localizacao = f"Remoto ({lugar})" if lugar else "Remoto"
    elif tipo in ("hybrid", "on-site", "onsite"):
        remoto = False  # mesma semântica da fonte Lever: híbrido conta como não remoto
        localizacao = lugar
    else:
        remoto = None
        localizacao = lugar

    return Vaga(
        id_fonte=str(id_),
        fonte="gupy",
        empresa=str(empresa).strip(),
        cargo=str(nome).strip(),
        localizacao=localizacao,
        remoto=remoto,
        descricao=html_para_texto(str(item.get("description") or "")),
        url=url,
        data=_data(item.get("publishedDate")),
    )
