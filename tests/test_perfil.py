import pytest
import yaml
from pydantic import ValidationError

from bot_vagas.perfil import PERFIL_PADRAO, Perfil


def test_perfil_padrao_carrega(perfil):
    assert perfil.nome == "THIAGO MATEUS"
    assert "@" in perfil.contato.email  # contato real fica fora do git; o exemplo tem e-mail fictício
    assert len(perfil.experiencia) == 1
    assert perfil.experiencia[0].empresa == "LIFETRAILER STUDIO"
    assert perfil.experiencia[0].periodo.pt == "07/2026 – atual"
    assert len(perfil.projetos) == 4
    assert "typescript" in perfil.ids_tecnologias()


def test_ids_unicos(perfil):
    ids = perfil.todos_ids()
    assert len(ids) == len(set(ids))


def test_textos_em_ambos_idiomas(perfil):
    for idioma in ("pt", "en"):
        textos = perfil.textos(idioma)
        assert all(isinstance(t, str) and t for t in textos)
    assert "SOBRE MIM" in perfil.textos("pt")
    assert "SUMMARY" in perfil.textos("en")


def _dados_brutos():
    with open(PERFIL_PADRAO, encoding="utf-8") as f:
        return yaml.safe_load(f)


def test_rejeita_campo_extra():
    dados = _dados_brutos()
    dados["campo_inventado"] = "x"
    with pytest.raises(ValidationError):
        Perfil.model_validate(dados)


def test_rejeita_id_duplicado():
    dados = _dados_brutos()
    dados["projetos"][1]["id"] = dados["projetos"][0]["id"]
    with pytest.raises(ValidationError, match="IDs duplicados"):
        Perfil.model_validate(dados)


def test_rejeita_texto_sem_traducao():
    dados = _dados_brutos()
    del dados["resumo"]["en"]
    with pytest.raises(ValidationError):
        Perfil.model_validate(dados)


def test_rejeita_email_invalido():
    dados = _dados_brutos()
    dados["contato"]["email"] = "sem-arroba"
    with pytest.raises(ValidationError):
        Perfil.model_validate(dados)
