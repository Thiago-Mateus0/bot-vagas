"""Utilitários de texto: HTML -> texto plano e normalização para comparação."""

from __future__ import annotations

import html
import re
import unicodedata
from html.parser import HTMLParser


class _Extrator(HTMLParser):
    _BLOCO = {"p", "br", "li", "div", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "tr"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.partes: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in self._BLOCO:
            self.partes.append("\n")

    def handle_endtag(self, tag):
        if tag in self._BLOCO:
            self.partes.append("\n")

    def handle_data(self, data):
        self.partes.append(data)


def html_para_texto(conteudo: str) -> str:
    if not conteudo:
        return ""
    p = _Extrator()
    try:
        p.feed(conteudo)
        p.close()
    except Exception:  # HTML quebrado: fica com o texto cru sem tags
        return re.sub(r"<[^>]+>", " ", html.unescape(conteudo)).strip()
    texto = "".join(p.partes)
    texto = re.sub(r"[ \t\r\f\v]+", " ", texto)
    texto = re.sub(r"\n\s*\n+", "\n", texto)
    return texto.strip()


def normalizar(texto: str) -> str:
    """minúsculas, sem acento, espaços colapsados."""
    nfkd = unicodedata.normalize("NFKD", texto or "")
    sem_acento = "".join(c for c in nfkd if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", sem_acento.lower()).strip()
