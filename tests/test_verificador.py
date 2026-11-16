import pytest

from bot_vagas.curriculo import texto_do_curriculo
from bot_vagas.plano import PlanoAdaptacao, plano_neutro
from bot_vagas.verificador import Violacao, verificar, verificar_plano, verificar_texto


def _plano(perfil, idioma="pt", **mudancas):
    return PlanoAdaptacao(**{**plano_neutro(perfil, idioma).model_dump(), **mudancas})


@pytest.mark.parametrize("idioma", ["pt", "en"])
def test_aceita_curriculo_neutro_completo(perfil, idioma):
    plano = plano_neutro(perfil, idioma)
    assert verificar(plano, perfil, texto_do_curriculo(perfil, plano)) == []


def test_aceita_plano_valido_reordenado(perfil):
    plano = _plano(
        perfil,
        ordem_competencias=["python", "fastapi", "postgresql"],
        bullets_destaque=["exp_ia", "exp_altegio"],
        ordem_projetos=["arkhe_finance"],
        resumo=(
            "Estudante de Ciência da Computação e desenvolvedor júnior com experiência real em produção: "
            "integrei IA ao sistema com a API da Anthropic e criei regras de negócio em PostgreSQL. "
            "Trabalho com Python, FastAPI e TypeScript e uso Claude Code no dia a dia. "
            "Busco estágio ou a primeira vaga como desenvolvedor júnior."
        ),
    )
    assert verificar(plano, perfil, texto_do_curriculo(perfil, plano)) == []


def test_aceita_resumo_em_ingles(perfil):
    plano = _plano(
        perfil,
        idioma="en",
        resumo="Junior developer who integrated AI with the Anthropic API and works with Python and PostgreSQL. I started programming in 2026.",
    )
    assert verificar(plano, perfil) == []


def test_rejeita_machine_learning(perfil):
    v = verificar_texto("Tenho experiência em Machine Learning com PyTorch.", perfil, "pt")
    tipos = {x.tipo for x in v}
    assert "termo_proibido" in tipos
    assert any("machine learning" in x.detalhe for x in v)
    assert any("pytorch" in x.detalhe for x in v)


def test_rejeita_termo_minusculo_proibido(perfil):
    v = verificar_texto("uso pandas para análise.", perfil, "pt")
    assert any(x.tipo == "termo_proibido" and "pandas" in x.detalhe for x in v)


def test_rejeita_empresa_falsa(perfil):
    v = verificar_texto("Trabalhei como desenvolvedor na Google e na Nubank Labs.", perfil, "pt")
    assert any(x.tipo == "termo_desconhecido" and "'Google'" in x.detalhe for x in v)
    assert any("'Labs'" in x.detalhe for x in v)


def test_rejeita_data_trocada(perfil):
    v = verificar_texto("Na Lifetrailer Studio desde 03/2025, programo desde 2024.", perfil, "pt")
    datas = {x.detalhe for x in v if x.tipo == "data"}
    assert any("03/2025" in d for d in datas)
    assert any("2024" in d for d in datas)


def test_rejeita_numero_inventado(perfil):
    v = verificar_texto("Sistema com 20 módulos e 3 anos de experiência.", perfil, "pt")
    numeros = {x.detalhe for x in v if x.tipo == "numero"}
    assert any("'20'" in d for d in numeros)
    assert any("'3'" in d for d in numeros)


def test_rejeita_sigla_e_tecnologia_desconhecidas(perfil):
    v = verificar_texto("Experiência com AWS, Kubernetes e Vue.js.", perfil, "pt")
    detalhes = " ".join(x.detalhe for x in v)
    assert "AWS" in detalhes and "Kubernetes" in detalhes and "Vue.js" in detalhes


def test_rejeita_cargo_inventado(perfil):
    v = verificar_texto("Atuo como Desenvolvedor Sênior e Tech Lead.", perfil, "pt")
    detalhes = " ".join(x.detalhe for x in v)
    assert "sênior" in detalhes and "Tech" in detalhes and "Lead" in detalhes


def test_rejeita_id_inexistente_no_plano(perfil):
    plano = _plano(perfil, ordem_competencias=["python", "tensorflow"], bullets_destaque=["exp_fake"], ordem_projetos=["x"])
    v = verificar_plano(plano, perfil)
    assert {x.tipo for x in v} == {"id_invalido"}
    assert len(v) == 3


def test_rejeita_id_repetido_no_plano(perfil):
    plano = _plano(perfil, ordem_competencias=["python", "python"])
    v = verificar_plano(plano, perfil)
    assert any(x.tipo == "id_repetido" for x in v)


def test_resumo_inventado_reprova_plano(perfil):
    plano = _plano(perfil, resumo="Desenvolvedor com 5 anos de experiência em Machine Learning na Google.")
    v = verificar(plano, perfil)
    assert v, "deveria haver violações"
    assert {x.tipo for x in v} >= {"termo_proibido", "numero", "termo_desconhecido"}


def test_camada_llm_injetavel(perfil):
    chamadas = []

    def falso_llm(texto, perfil_, idioma):
        chamadas.append((idioma, len(texto)))
        return [Violacao("llm", "afirmação suspeita")]

    plano = plano_neutro(perfil, "pt")
    v = verificar(plano, perfil, texto_do_curriculo(perfil, plano), verificador_llm=falso_llm)
    assert v == [Violacao("llm", "afirmação suspeita")]
    assert chamadas and chamadas[0][0] == "pt"
