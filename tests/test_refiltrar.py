"""`bot-vagas filtrar --refazer`: reaplica a fase A quando as regras mudam, sem tocar em vagas com nota."""

import json

import pytest

from bot_vagas import cli
from bot_vagas.banco import Banco, chave_vaga
from bot_vagas.filtro import Filtros
from bot_vagas.fontes import Vaga
from bot_vagas.pipeline import filtrar, refiltrar

# Regra antiga: só remoto. Regra nova: remoto OU presencial em Maceió.
SO_REMOTO = Filtros(termos_remoto=("remoto", "remote"), niveis_aceitos=("estagio",))
REMOTO_OU_MACEIO = Filtros(termos_remoto=("remoto", "remote"), niveis_aceitos=("estagio",),
                           termos_presencial=("presencial", "hibrido"), cidades_presencial=("maceio",),
                           estados_siglas=("al",))


def vaga(n: int, **kw) -> Vaga:
    base = dict(id_fonte=str(n), fonte="greenhouse", empresa=f"Emp{n}", cargo="Estágio Backend",
                localizacao="Remote", remoto=None, descricao="Python.",
                url=f"https://boards.greenhouse.io/emp{n}/jobs/{n}")
    base.update(kw)
    return Vaga(**base)


VAGAS = [
    vaga(1, localizacao="Presencial - Maceió, AL"),   # bloqueada pela antiga -> nova pela nova
    vaga(2, localizacao="Remote"),                    # nova nas duas
    vaga(3, localizacao="Presencial - Recife, PE"),   # bloqueada nas duas
    vaga(4, localizacao="Remote"),                    # vai receber nota (aprovada)
    vaga(5, localizacao="Remote"),                    # vai receber nota (abaixo_do_corte)
    vaga(6, localizacao="Presencial - Maceió, AL"),   # nova sem detalhes, mas com nota marcada à mão
]


def chave(n: int) -> str:
    v = VAGAS[n - 1]
    return chave_vaga(v.empresa, v.cargo, v.url)


@pytest.fixture
def banco_preparado(tmp_path):
    caminho = tmp_path / "t.db"
    with Banco(caminho) as b:
        b.salvar_vagas(VAGAS)
        filtrar(b, SO_REMOTO, log=lambda *a, **k: None)
        assert b.buscar_por_chave(chave(1))["status"] == "bloqueada"
        assert b.buscar_por_chave(chave(2))["status"] == "nova"
        assert b.buscar_por_chave(chave(3))["status"] == "bloqueada"
        # simula fase B já feita (com gasto registrado) em 4 e 5; 6 tem nota mas continua 'nova'
        b.atualizar_status(chave(4), "aprovada", nota=8.0, motivo="ótima")
        b.registrar_chamada(modelo="m", finalidade="avaliar", chave_vaga=chave(4), tokens_entrada=1,
                            tokens_saida=1, tokens_cache_leitura=0, tokens_cache_escrita=0, custo_usd=0.01)
        b.atualizar_status(chave(5), "abaixo_do_corte", nota=4.0, motivo="fraca")
        b.atualizar_status(chave(6), "nova", nota=6.0, motivo="nota manual")
    return caminho


def test_refazer_aplica_regra_nova_e_preserva_vagas_com_nota(banco_preparado):
    with Banco(banco_preparado) as b:
        antes = {n: dict(b.buscar_por_chave(chave(n))) for n in (4, 5, 6)}
        total = refiltrar(b, REMOTO_OU_MACEIO, log=lambda *a, **k: None)

        assert total == {"bloqueada_para_nova": 1, "nova_para_bloqueada": 0, "iguais": 2, "ignoradas_com_nota": 3}

        v1 = b.buscar_por_chave(chave(1))
        assert v1["status"] == "nova"
        assert "Maceió" in v1["motivo"]
        assert json.loads(v1["detalhes"])["filtro"] == "passou"

        assert b.buscar_por_chave(chave(2))["status"] == "nova"
        v3 = b.buscar_por_chave(chave(3))
        assert v3["status"] == "bloqueada"
        assert "fora de Maceió" in v3["motivo"]

        for n in (4, 5, 6):
            assert dict(b.buscar_por_chave(chave(n))) == antes[n]
        assert b.resumo_gasto()["chamadas"] == 1  # nenhuma chamada nova ao modelo


def test_refazer_no_sentido_inverso_bloqueia_nova(banco_preparado):
    with Banco(banco_preparado) as b:
        refiltrar(b, REMOTO_OU_MACEIO, log=lambda *a, **k: None)
        atualizado_antes = b.buscar_por_chave(chave(1))["atualizado_em"]
        # regra volta a ser só remoto: a vaga 1 (Maceió) deve voltar a bloqueada
        total = refiltrar(b, SO_REMOTO, log=lambda *a, **k: None)
        assert total["nova_para_bloqueada"] == 1
        assert total["bloqueada_para_nova"] == 0
        assert total["ignoradas_com_nota"] == 3
        v1 = b.buscar_por_chave(chave(1))
        assert v1["status"] == "bloqueada"
        assert "remoto não identificado" in v1["motivo"]
        assert v1["atualizado_em"] >= atualizado_antes
        assert "pontos" not in json.loads(v1["detalhes"])


def test_refazer_e_idempotente(banco_preparado):
    with Banco(banco_preparado) as b:
        refiltrar(b, REMOTO_OU_MACEIO, log=lambda *a, **k: None)
        total = refiltrar(b, REMOTO_OU_MACEIO, log=lambda *a, **k: None)
        assert total["bloqueada_para_nova"] == 0 and total["nova_para_bloqueada"] == 0
        assert total["iguais"] == 3 and total["ignoradas_com_nota"] == 3


def test_cli_filtrar_refazer(banco_preparado, tmp_path, capsys):
    filtros_yaml = tmp_path / "filtros.yaml"
    filtros_yaml.write_text(
        "remoto:\n  termos: [remoto, remote]\n"
        "localizacao_aceita:\n  termos_presencial: [presencial, hibrido]\n  cidades_presencial: [maceio]\n"
        "  estados_siglas: [al]\n"
        "nivel:\n  aceitos: [estagio]\n",
        encoding="utf-8",
    )
    assert cli.main(["filtrar", "--refazer", "--banco", str(banco_preparado), "--filtros", str(filtros_yaml)]) == 0
    saida = capsys.readouterr().out
    assert "1 bloqueada->nova" in saida
    assert "0 nova->bloqueada" in saida
    assert "2 permaneceram iguais" in saida
    assert "3 ignoradas por já terem nota" in saida
    with Banco(banco_preparado) as b:
        assert b.buscar_por_chave(chave(1))["status"] == "nova"
        assert b.buscar_por_chave(chave(4))["status"] == "aprovada"
