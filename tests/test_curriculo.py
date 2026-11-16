import pytest
from pypdf import PdfReader

from bot_vagas.curriculo import (
    CurriculoNaoCabeEmUmaPagina,
    contar_paginas,
    gerar_curriculo,
    renderizar_html,
    texto_do_curriculo,
)
from bot_vagas.plano import PlanoAdaptacao, plano_neutro


@pytest.mark.parametrize("idioma", ["pt", "en"])
def test_gera_pdf_de_uma_pagina(perfil, idioma, tmp_path):
    destino = tmp_path / f"cv-{idioma}.pdf"
    pdf = gerar_curriculo(perfil, plano_neutro(perfil, idioma), destino)
    assert destino.exists()
    assert contar_paginas(pdf) == 1
    pagina = PdfReader(destino).pages[0]
    largura, altura = float(pagina.mediabox.width), float(pagina.mediabox.height)
    assert abs(largura - 595) < 2 and abs(altura - 842) < 2  # A4 em pontos
    texto = pagina.extract_text()
    assert perfil.nome in texto
    assert perfil.experiencia[0].empresa in texto
    assert perfil.contato.email in texto


def test_falha_quando_estoura_uma_pagina(perfil):
    plano = plano_neutro(perfil, "pt")
    plano_grande = PlanoAdaptacao(**{**plano.model_dump(), "resumo": plano.resumo + " Texto adicional." * 120})
    with pytest.raises(CurriculoNaoCabeEmUmaPagina) as exc:
        gerar_curriculo(perfil, plano_grande)
    assert exc.value.paginas >= 2
    assert "1" in str(exc.value)


def test_plano_reordena_e_destaca(perfil):
    plano = plano_neutro(perfil, "pt")
    plano = PlanoAdaptacao(
        **{
            **plano.model_dump(),
            "ordem_competencias": ["python", "fastapi"],
            "bullets_destaque": ["exp_ia"],
            "ordem_projetos": ["extrator_enem"],
        }
    )
    html = renderizar_html(perfil, plano)
    texto = texto_do_curriculo(perfil, plano)
    # Python e FastAPI vêm antes de TypeScript na lista de competências
    assert texto.index("Python, FastAPI, TypeScript") > 0
    # bullet destacado vem primeiro e em negrito
    assert html.index('class="destaque"') < html.index("Desenvolvo e mantenho")
    assert texto.index("Integrei IA") < texto.index("Desenvolvo e mantenho")
    # projeto reordenado
    assert texto.index("Extrator ENEM") < texto.index("Arkhé Finance")


def test_html_escapa_conteudo(perfil):
    plano = plano_neutro(perfil, "pt")
    plano = PlanoAdaptacao(**{**plano.model_dump(), "resumo": "<script>alert(1)</script>"})
    html = renderizar_html(perfil, plano)
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
