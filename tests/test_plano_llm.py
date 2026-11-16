import json
from types import SimpleNamespace

import pytest

from bot_vagas.plano import PlanoAdaptacao, plano_neutro
from bot_vagas.plano_llm import MODELO, PlanoInvalido, PlanoRecusado, gerar_plano


class ClienteFalso:
    """Imita `anthropic.Anthropic().messages.create` sem rede."""

    def __init__(self, resposta):
        self.resposta = resposta
        self.chamadas = []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.chamadas.append(kwargs)
        return self.resposta


def _resposta(texto, stop_reason="end_turn", stop_details=None):
    return SimpleNamespace(
        stop_reason=stop_reason,
        stop_details=stop_details,
        content=[SimpleNamespace(type="text", text=texto)],
    )


def _plano_bom(perfil, idioma="pt", **mud):
    base = plano_neutro(perfil, idioma).model_dump()
    base.update(ordem_competencias=["python", "fastapi", "postgresql"], bullets_destaque=["exp_ia"])
    base.update(mud)
    return base


def test_gera_plano_com_cliente_falso(perfil):
    cliente = ClienteFalso(_resposta(json.dumps(_plano_bom(perfil))))
    plano = gerar_plano(perfil, "Vaga de estágio backend Python com FastAPI e PostgreSQL.", "pt", cliente)

    assert isinstance(plano, PlanoAdaptacao)
    assert plano.ordem_competencias[:3] == ["python", "fastapi", "postgresql"]
    assert plano.bullets_destaque == ["exp_ia"]

    assert len(cliente.chamadas) == 1
    kwargs = cliente.chamadas[0]
    assert kwargs["model"] == MODELO == "claude-haiku-5-5"
    for proibido in ("temperature", "top_p", "top_k"):
        assert proibido not in kwargs
    assert kwargs["messages"][-1]["role"] == "user"  # sem prefill de assistant
    fmt = kwargs["output_config"]["format"]
    assert fmt["type"] == "json_schema"
    assert fmt["schema"]["additionalProperties"] is False
    assert set(fmt["schema"]["required"]) == set(PlanoAdaptacao.model_fields)
    assert kwargs["output_config"]["effort"] == "low"
    # dados de contato nunca vão para o prompt
    system_txt = json.dumps(kwargs["system"], ensure_ascii=False)
    assert perfil.contato.email not in system_txt
    assert perfil.contato.telefone not in system_txt
    assert "DESCRIÇÃO DA VAGA" in kwargs["messages"][0]["content"]


def test_trata_refusal(perfil):
    cliente = ClienteFalso(_resposta("", stop_reason="refusal", stop_details=SimpleNamespace(category="x", explanation="não")))
    with pytest.raises(PlanoRecusado, match="não"):
        gerar_plano(perfil, "vaga", "pt", cliente)


def test_trata_max_tokens(perfil):
    cliente = ClienteFalso(_resposta("{", stop_reason="max_tokens"))
    with pytest.raises(PlanoInvalido, match="max_tokens"):
        gerar_plano(perfil, "vaga", "pt", cliente)


def test_parse_defensivo_com_texto_em_volta(perfil):
    texto = "Segue o plano:\n" + json.dumps(_plano_bom(perfil)) + "\nEspero ter ajudado."
    plano = gerar_plano(perfil, "vaga", "pt", ClienteFalso(_resposta(texto)))
    assert plano.idioma == "pt"


def test_json_invalido(perfil):
    with pytest.raises(PlanoInvalido, match="JSON"):
        gerar_plano(perfil, "vaga", "pt", ClienteFalso(_resposta("isso não é json")))


def test_schema_invalido(perfil):
    with pytest.raises(PlanoInvalido, match="schema"):
        gerar_plano(perfil, "vaga", "pt", ClienteFalso(_resposta(json.dumps({"idioma": "pt"}))))


def test_plano_com_fato_inventado_e_reprovado(perfil):
    dados = _plano_bom(perfil, resumo="Especialista em Machine Learning na Google desde 2020.")
    with pytest.raises(PlanoInvalido) as exc:
        gerar_plano(perfil, "vaga", "pt", ClienteFalso(_resposta(json.dumps(dados))))
    assert exc.value.violacoes
    assert "machine learning" in str(exc.value)


def test_plano_com_id_inexistente_e_reprovado(perfil):
    dados = _plano_bom(perfil, ordem_competencias=["python", "tensorflow"])
    with pytest.raises(PlanoInvalido, match="tensorflow"):
        gerar_plano(perfil, "vaga", "pt", ClienteFalso(_resposta(json.dumps(dados))))


def test_idioma_diferente_do_pedido(perfil):
    dados = _plano_bom(perfil, idioma="en")
    with pytest.raises(PlanoInvalido, match="idioma|'en'"):
        gerar_plano(perfil, "vaga", "pt", ClienteFalso(_resposta(json.dumps(dados))))


def test_descricao_vazia(perfil):
    with pytest.raises(ValueError):
        gerar_plano(perfil, "   ", "pt", ClienteFalso(None))
