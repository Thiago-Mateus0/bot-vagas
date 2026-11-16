"""Gerador de currículo: perfil + plano -> HTML -> PDF (Playwright, A4) com 1 página garantida."""

from __future__ import annotations

import io
from html import escape
from pathlib import Path

from pypdf import PdfReader

from .perfil import Idioma, Perfil, texto_em
from .plano import PlanoAdaptacao, ordenar_por_ids

TEMPLATES = Path(__file__).parent / "templates"


class CurriculoNaoCabeEmUmaPagina(RuntimeError):
    def __init__(self, paginas: int):
        super().__init__(
            f"o currículo gerado tem {paginas} páginas; o limite é exatamente 1. "
            "Encurte o resumo ou reduza o conteúdo do plano (nada é cortado automaticamente)."
        )
        self.paginas = paginas


def _link(url: str, texto: str | None = None) -> str:
    href = url if url.startswith(("http://", "https://", "mailto:")) else f"https://{url}"
    return f'<a href="{escape(href, quote=True)}">{escape(texto or url)}</a>'


def _bullets(itens: list[tuple[str, bool]]) -> str:
    lis = []
    for html_interno, destaque in itens:
        cls = ' class="destaque"' if destaque else ""
        lis.append(f"<li{cls}>{html_interno}</li>")
    return "<ul>" + "".join(lis) + "</ul>"


def renderizar_html(perfil: Perfil, plano: PlanoAdaptacao) -> str:
    """Monta o HTML completo do currículo. Todo texto passa por escape."""
    idioma: Idioma = plano.idioma
    s = perfil.secoes
    c = perfil.contato
    destaques = set(plano.bullets_destaque)
    css = (TEMPLATES / "curriculo.css").read_text(encoding="utf-8")

    partes: list[str] = []
    partes.append(
        "<header>"
        f"<h1>{escape(perfil.nome)}</h1>"
        f'<div class="titulo">{escape(perfil.titulo.em(idioma))}</div>'
        '<div class="contato">'
        + '<span class="sep">•</span>'.join(
            [
                escape(c.cidade.em(idioma)),
                _link(f"mailto:{c.email}", c.email),
                escape(c.telefone),
                _link(c.linkedin),
                _link(c.github),
            ]
        )
        + "</div></header>"
    )

    # Sobre mim (resumo vindo do plano)
    partes.append(f"<h2>{escape(s.sobre.em(idioma))}</h2><p>{escape(plano.resumo)}</p>")

    # Habilidades interpessoais
    partes.append(f"<h2>{escape(s.interpessoais.em(idioma))}</h2>")
    partes.append(
        _bullets(
            [
                (f"<strong>{escape(h.titulo.em(idioma))}:</strong> {escape(h.texto.em(idioma))}", False)
                for h in perfil.habilidades_interpessoais
            ]
        )
    )

    # Educação
    partes.append(f"<h2>{escape(s.educacao.em(idioma))}</h2>")
    for e in perfil.educacao:
        partes.append(
            '<div class="bloco"><div class="linha">'
            f'<span class="esq">{escape(e.curso.em(idioma))} — {escape(e.instituicao)}, {escape(e.local.em(idioma))}</span>'
            f'<span class="dir">{escape(e.periodo.em(idioma))}</span></div>'
        )
        bullets = ordenar_por_ids(e.bullets, [b for b in plano.bullets_destaque if b in {x.id for x in e.bullets}])
        partes.append(_bullets([(f"<em>{escape(b.texto.em(idioma))}</em>", b.id in destaques) for b in bullets]))
        partes.append("</div>")

    # Competências técnicas
    tecnologias = ordenar_por_ids(perfil.competencias.tecnologias, plano.ordem_competencias)
    lista_tec = ", ".join(escape(t.nome.em(idioma)) for t in tecnologias) + "."
    sep_idiomas = " e " if idioma == "pt" else " and "
    lista_idiomas = sep_idiomas.join(
        f"{escape(i.nome.em(idioma))} ({escape(i.nivel.em(idioma))})" for i in perfil.competencias.idiomas
    )
    partes.append(f"<h2>{escape(s.competencias.em(idioma))}</h2>")
    partes.append(
        _bullets(
            [
                (lista_tec, False),
                (f"<strong>{escape(s.idiomas.em(idioma))}:</strong> {lista_idiomas}.", False),
            ]
        )
    )

    # Experiência
    partes.append(f"<h2>{escape(s.experiencia.em(idioma))}</h2>")
    for x in perfil.experiencia:
        partes.append(
            '<div class="bloco">'
            f'<div class="linha"><span class="esq">{escape(x.empresa)}</span><span class="dir">{escape(x.local.em(idioma))}</span></div>'
            f'<div class="linha sub"><span class="esq">{escape(x.cargo.em(idioma))}</span><span class="dir">{escape(x.periodo.em(idioma))}</span></div>'
        )
        ids_aqui = {b.id for b in x.bullets}
        bullets = ordenar_por_ids(x.bullets, [b for b in plano.bullets_destaque if b in ids_aqui])
        partes.append(_bullets([(_com_links(b.texto.em(idioma)), b.id in destaques) for b in bullets]))
        partes.append("</div>")

    # Projetos
    projetos = ordenar_por_ids(perfil.projetos, plano.ordem_projetos)
    partes.append(f"<h2>{escape(s.projetos.em(idioma))}</h2>")
    partes.append(
        _bullets(
            [
                (f"<strong>{escape(texto_em(p.nome, idioma))}:</strong> {escape(p.descricao.em(idioma))}", False)
                for p in projetos
            ]
        )
    )

    return (
        f'<!DOCTYPE html><html lang="{idioma}"><head><meta charset="utf-8">'
        f"<title>{escape(perfil.nome)}</title><style>{css}</style></head><body>"
        + "".join(partes)
        + "</body></html>"
    )


def _com_links(texto: str) -> str:
    """Escapa o texto e transforma URLs github.com/... em links (como no PDF original)."""
    import re

    partes = []
    pos = 0
    for m in re.finditer(r"github\.com/[\w./-]+", texto):
        partes.append(escape(texto[pos : m.start()]))
        url = m.group(0).rstrip(".")
        partes.append(_link(url))
        partes.append(escape(m.group(0)[len(url) :]))
        pos = m.end()
    partes.append(escape(texto[pos:]))
    return "".join(partes)


def html_para_pdf(html: str) -> bytes:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        navegador = p.chromium.launch()
        try:
            pagina = navegador.new_page()
            pagina.set_content(html, wait_until="load")
            return pagina.pdf(format="A4", print_background=True, prefer_css_page_size=True)
        finally:
            navegador.close()


def contar_paginas(pdf: bytes) -> int:
    return len(PdfReader(io.BytesIO(pdf)).pages)


def gerar_curriculo(perfil: Perfil, plano: PlanoAdaptacao, destino: Path | str | None = None) -> bytes:
    """Gera o PDF do currículo e garante exatamente 1 página.

    Levanta CurriculoNaoCabeEmUmaPagina se o conteúdo estourar; nunca corta texto.
    """
    html = renderizar_html(perfil, plano)
    pdf = html_para_pdf(html)
    paginas = contar_paginas(pdf)
    if paginas != 1:
        raise CurriculoNaoCabeEmUmaPagina(paginas)
    if destino is not None:
        destino = Path(destino)
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_bytes(pdf)
    return pdf


def texto_do_curriculo(perfil: Perfil, plano: PlanoAdaptacao) -> str:
    """Texto plano do currículo adaptado (entrada do verificador de fatos), sem renderizar PDF."""
    import re

    html = renderizar_html(perfil, plano)
    html = re.sub(r"<style>.*?</style>", " ", html, flags=re.S)
    html = re.sub(r"<(li|p|h1|h2|div|br)[^>]*>", "\n", html)
    texto = re.sub(r"<[^>]+>", " ", html)
    import html as _html

    return _html.unescape(texto)
