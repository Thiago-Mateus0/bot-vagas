"""Cliente HTTP mínimo (stdlib) para as APIs públicas de quadros de vagas.

- timeout fixo, pausa curta entre requisições, User-Agent genérico;
- NUNCA envia e-mail, nome ou qualquer dado pessoal;
- só aceita https;
- `get_html` (usado pela Gupy) devolve status + corpo + URL final e recusa redirecionamento
  para fora dos hosts permitidos.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlsplit

USER_AGENT = "bot-vagas/0.1"
# Página pública da Gupy: um UA de navegador comum, com o nome do bot anexado (sem dados pessoais).
USER_AGENT_NAVEGADOR = "Mozilla/5.0 (X11; Linux x86_64) bot-vagas/0.1"
TIMEOUT_S = 20
PAUSA_S = 1.5


class ErroHTTP(RuntimeError):
    pass


@dataclass(frozen=True)
class RespostaHTML:
    status: int
    texto: str
    url_final: str


class ClienteHTTP(Protocol):
    def get_json(self, url: str) -> Any: ...

    def get_html(self, url: str, hosts_permitidos: tuple[str, ...] = ()) -> RespostaHTML: ...


class RedirecionamentoRestrito(urllib.request.HTTPRedirectHandler):
    """Só segue redirecionamento https para um dos hosts permitidos; senão levanta ErroHTTP."""

    def __init__(self, hosts_permitidos: tuple[str, ...]):
        super().__init__()
        self.hosts_permitidos = tuple(h.lower() for h in hosts_permitidos)

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        partes = urlsplit(newurl)
        host = (partes.hostname or "").lower()
        if partes.scheme != "https" or (self.hosts_permitidos and host not in self.hosts_permitidos):
            raise ErroHTTP(f"redirecionamento recusado para {newurl} (host fora de {self.hosts_permitidos})")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class ClienteHTTPReal:
    """Faz requisições de verdade. Só é instanciado pelo comando `buscar`."""

    def __init__(self, timeout: float = TIMEOUT_S, pausa: float = PAUSA_S):
        self.timeout = timeout
        self.pausa = pausa
        self._ultima = 0.0

    def _esperar(self) -> None:
        espera = self.pausa - (time.monotonic() - self._ultima)
        if espera > 0:
            time.sleep(espera)

    def get_json(self, url: str) -> Any:
        if not url.startswith("https://"):
            raise ErroHTTP(f"só https é permitido: {url}")
        self._esperar()
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # noqa: S310 (https checado acima)
                corpo = resp.read()
        except urllib.error.HTTPError as e:
            raise ErroHTTP(f"HTTP {e.code} em {url}") from None
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise ErroHTTP(f"falha de rede em {url}: {e}") from None
        finally:
            self._ultima = time.monotonic()
        try:
            return json.loads(corpo)
        except json.JSONDecodeError as e:
            raise ErroHTTP(f"resposta não é JSON em {url}: {e}") from None

    def get_html(self, url: str, hosts_permitidos: tuple[str, ...] = ()) -> RespostaHTML:
        """Baixa uma página HTML. HTTP != 2xx NÃO levanta: devolve o status para a fonte decidir.
        Falha de rede e redirecionamento fora dos hosts permitidos levantam ErroHTTP."""
        if not url.startswith("https://"):
            raise ErroHTTP(f"só https é permitido: {url}")
        host = (urlsplit(url).hostname or "").lower()
        if hosts_permitidos and host not in tuple(h.lower() for h in hosts_permitidos):
            raise ErroHTTP(f"host não permitido: {url}")
        self._esperar()
        abridor = urllib.request.build_opener(RedirecionamentoRestrito(hosts_permitidos))
        req = urllib.request.Request(
            url,
            headers={"User-Agent": USER_AGENT_NAVEGADOR, "Accept": "text/html,application/xhtml+xml",
                     "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.5"},
        )
        try:
            with abridor.open(req, timeout=self.timeout) as resp:  # noqa: S310 (https checado acima)
                corpo = resp.read()
                return RespostaHTML(resp.status, corpo.decode("utf-8", errors="replace"), resp.geturl())
        except urllib.error.HTTPError as e:
            try:
                corpo = e.read()
            except Exception:
                corpo = b""
            return RespostaHTML(e.code, corpo.decode("utf-8", errors="replace"), url)
        except ErroHTTP:
            raise
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise ErroHTTP(f"falha de rede em {url}: {e}") from None
        finally:
            self._ultima = time.monotonic()


class ClienteHTTPFalso:
    """Para testes: devolve respostas gravadas por URL (ou por prefixo).

    Valores: para `get_json`, o JSON; para `get_html`, uma `str` (HTML, status 200), uma
    `RespostaHTML` ou uma instância de exceção (que é levantada)."""

    def __init__(self, respostas: dict[str, Any]):
        self.respostas = respostas
        self.chamadas: list[str] = []

    def _achar(self, url: str) -> Any:
        if url in self.respostas:
            return self.respostas[url]
        for chave, valor in self.respostas.items():
            if url.startswith(chave):
                return valor
        raise ErroHTTP(f"URL sem resposta gravada: {url}")

    def get_json(self, url: str) -> Any:
        self.chamadas.append(url)
        return self._achar(url)

    def get_html(self, url: str, hosts_permitidos: tuple[str, ...] = ()) -> RespostaHTML:
        self.chamadas.append(url)
        valor = self._achar(url)
        if isinstance(valor, Exception):
            raise valor
        if isinstance(valor, RespostaHTML):
            return valor
        return RespostaHTML(200, str(valor), url)
