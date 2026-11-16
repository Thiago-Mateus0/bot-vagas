import pytest

from bot_vagas.banco import Banco, chave_vaga, normalizar_url
from bot_vagas.filtro import FILTROS_PADRAO, Filtros, avaliar_palavras_chave, carregar_filtros
from bot_vagas.fontes import Vaga


def vaga(**kw) -> Vaga:
    base = dict(id_fonte="1", fonte="greenhouse", empresa="Acme", cargo="Estágio Backend",
                localizacao="Remote", remoto=None, descricao="Python, PostgreSQL, integração com LLM.",
                url="https://boards.greenhouse.io/acme/jobs/1")
    base.update(kw)
    return Vaga(**base)


@pytest.fixture
def filtros() -> Filtros:
    return carregar_filtros(FILTROS_PADRAO)


@pytest.fixture
def banco(tmp_path):
    with Banco(tmp_path / "t.db") as b:
        yield b


# --- dedupe ---------------------------------------------------------------

def test_normalizar_url_ignora_rastreadores_e_esquema():
    a = normalizar_url("https://www.boards.greenhouse.io/acme/jobs/1/?gh_src=xyz#top")
    b = normalizar_url("http://boards.greenhouse.io/acme/jobs/1")
    assert a == b == "boards.greenhouse.io/acme/jobs/1"


def test_chave_ignora_caixa_acento_e_espacos():
    assert chave_vaga("ACME", "Estágio  Backend", "https://x.com/a?utm=1") == chave_vaga("acme", "estagio backend", "https://x.com/a")


def test_banco_nao_reinsere_vaga_vista(banco):
    assert banco.salvar_vagas([vaga()]) == 1
    # mesma vaga com rastreador na URL e outra fonte: duplicata
    assert banco.salvar_vagas([vaga(fonte="lever", id_fonte="z", url="https://boards.greenhouse.io/acme/jobs/1?gh_src=abc")]) == 0
    assert banco.salvar_vagas([vaga(id_fonte="2", url="https://boards.greenhouse.io/acme/jobs/2")]) == 1
    assert len(banco.listar()) == 2
    assert banco.contar_por_status() == {"nova": 2}


def test_banco_atualiza_status_e_rejeita_invalido(banco):
    banco.salvar_vagas([vaga()])
    chave = banco.listar()[0]["chave"]
    banco.atualizar_status(chave, "aprovada", nota=8, motivo="ok")
    r = banco.listar("aprovada")[0]
    assert r["nota"] == 8 and r["motivo"] == "ok"
    with pytest.raises(ValueError):
        banco.atualizar_status(chave, "inventado")


# --- filtro por palavras-chave ----------------------------------------------

def test_filtro_passa_vaga_boa(filtros):
    r = avaliar_palavras_chave(vaga(), filtros)
    assert r.passou and r.pontos >= 3


def test_filtro_exige_remoto(filtros):
    r = avaliar_palavras_chave(vaga(localizacao="São Paulo", descricao="Python."), filtros)
    assert not r.passou and "remoto" in r.motivo
    # fonte afirma remoto -> passa mesmo sem o termo no texto
    assert avaliar_palavras_chave(vaga(localizacao="Brasil", remoto=True, descricao="Python."), filtros).passou
    # fonte afirma NÃO remoto e local fora de Maceió -> bloqueia mesmo com a palavra no texto
    r = avaliar_palavras_chave(vaga(remoto=False, descricao="remote friendly"), filtros)
    assert not r.passou and "não é remoto" in r.motivo and "fora de Maceió" in r.motivo


# --- localização: remoto OU presencial/híbrido em Maceió ---------------------

def test_local_remoto_aceito(filtros):
    r = avaliar_palavras_chave(vaga(localizacao="Remote - Brazil", descricao="estágio python"), filtros)
    assert r.passou and "remoto" in r.motivo


def test_local_presencial_maceio_aceito(filtros):
    r = avaliar_palavras_chave(vaga(localizacao="Maceió, AL", descricao="estágio presencial, python"), filtros)
    assert r.passou and "Maceió" in r.motivo
    # presencial no título, cidade na localização
    assert avaliar_palavras_chave(vaga(cargo="Estágio Backend (presencial)", localizacao="Maceió", descricao="python"), filtros).passou
    # fonte diz que não é remoto (lever onsite), mas é em Maceió
    assert avaliar_palavras_chave(vaga(localizacao="Maceio - Alagoas", remoto=False, descricao="estágio python"), filtros).passou


def test_local_hibrido_maceio_aceito(filtros):
    assert avaliar_palavras_chave(vaga(localizacao="Maceió (híbrido)", descricao="estágio python"), filtros).passou
    assert avaliar_palavras_chave(vaga(cargo="Hybrid Backend Intern", localizacao="Maceio, Brazil", descricao="python"), filtros).passou


def test_local_presencial_outra_cidade_bloqueado(filtros):
    r = avaliar_palavras_chave(vaga(localizacao="São Paulo, SP (presencial)", descricao="estágio python"), filtros)
    assert not r.passou and "presencial fora de Maceió" in r.motivo
    r = avaliar_palavras_chave(vaga(cargo="Estágio Backend - híbrido", localizacao="Rio de Janeiro", descricao="python"), filtros)
    assert not r.passou and "presencial fora de Maceió" in r.motivo
    # "Maceió" só na descrição (apoio fraco) NÃO salva vaga cujo cabeçalho diz outra cidade
    r = avaliar_palavras_chave(vaga(localizacao="Recife, PE", descricao="estágio presencial; a empresa também tem escritório em Maceió"), filtros)
    assert not r.passou


def test_local_nao_e_presencial_na_descricao_nao_bloqueia(filtros):
    r = avaliar_palavras_chave(vaga(localizacao="Remote", descricao="estágio 100% remoto; não é presencial nem híbrido"), filtros)
    assert r.passou


def test_local_formatos_de_maceio_reconhecidos(filtros):
    for loc in ("Maceió, AL", "Maceio - Alagoas", "Maceió/AL", "Alagoas", "AL"):
        r = avaliar_palavras_chave(vaga(localizacao=loc, remoto=False, descricao="estágio python"), filtros)
        assert r.passou, loc


def test_local_falso_positivo_nao_casa(filtros):
    for loc in ("Maceiozinho, PE", "Almaceio", "Salvador, BA", "Alagoinhas, BA", "Albany, NY"):
        r = avaliar_palavras_chave(vaga(localizacao=loc, remoto=False, descricao="estágio python"), filtros)
        assert not r.passou, loc


def test_local_remoto_restrito_fora_do_brasil(filtros):
    for loc in ("Remote - US only", "Remote (USA)", "Remote, United States only", "Remoto: Portugal"):
        r = avaliar_palavras_chave(vaga(localizacao=loc, descricao="internship python"), filtros)
        assert not r.passou and "fora do Brasil" in r.motivo, loc
    r = avaliar_palavras_chave(vaga(localizacao="Remote", descricao="internship; must be authorized to work in the US"), filtros)
    assert not r.passou and "fora do Brasil" in r.motivo
    # Brasil/LATAM não é restrição
    assert avaliar_palavras_chave(vaga(localizacao="Remote - Brazil only", descricao="estágio python"), filtros).passou
    assert avaliar_palavras_chave(vaga(localizacao="Remote (LATAM)", descricao="estágio python"), filtros).passou


def test_filtro_nivel(filtros):
    assert not avaliar_palavras_chave(vaga(cargo="Senior Backend Engineer", descricao="junior friendly remote"), filtros).passou
    assert not avaliar_palavras_chave(vaga(cargo="Desenvolvedor Pleno", descricao="junior remote"), filtros).passou
    assert not avaliar_palavras_chave(vaga(cargo="Backend Developer", descricao="remote python"), filtros).passou  # sem nível
    # AJUSTE: "Junior Backend Developer" e "Dev Jr" saíram desta lista de aceitos (júnior excluído por ora;
    # ver test_filtro_nivel_junior_e_graduate_bloqueados).
    for cargo in ("Trainee TI", "Software Engineering Intern", "Estágio em Desenvolvimento", "Trainee Backend",
                  "Software Engineer Intern", "Estagiário de TI", "Estagiária Backend"):
        assert avaliar_palavras_chave(vaga(cargo=cargo, descricao="remote python"), filtros).passou, cargo


def test_filtro_nivel_junior_e_graduate_bloqueados(filtros):
    for cargo in ("Junior Backend Developer", "Desenvolvedor Júnior", "Dev Jr", "Dev Jr.", "Graduate Developer",
                  "Early Career Engineer", "New Grad Software Engineer", "Entry Level Developer", "Entry-Level Developer"):
        r = avaliar_palavras_chave(vaga(cargo=cargo, descricao="remote python"), filtros)
        assert not r.passou and "nível não aceito" in r.motivo, cargo


def test_filtro_nivel_so_no_titulo(filtros):
    # nível na descrição não basta
    r = avaliar_palavras_chave(vaga(cargo="Backend Developer", descricao="vaga de estágio, remote, python"), filtros)
    assert not r.passou and "nível não identificado" in r.motivo
    # "intern" é palavra inteira: "internal"/"international" não casam
    for cargo in ("Internal Tools Engineer", "International Sales"):
        r = avaliar_palavras_chave(vaga(cargo=cargo, descricao="remote python"), filtros)
        assert not r.passou and "nível não identificado" in r.motivo, cargo


def test_filtro_nivel_estagio_vence_jr_no_titulo(filtros):
    # título com termo de estágio E "jr": aceita (estágio é o que ele quer)
    assert avaliar_palavras_chave(vaga(cargo="Estágio Dev Jr", descricao="remote python"), filtros).passou


# --- localização: "remote" só na descrição não vale para cidade específica -------

def test_local_remoto_so_na_descricao_com_cidade_especifica_bloqueia(filtros):
    for loc in ("Austin, TX", "London", "São Paulo, SP"):
        r = avaliar_palavras_chave(vaga(cargo="Software Engineer Intern", localizacao=loc,
                                        descricao="remote friendly team, python"), filtros)
        assert not r.passou and "remoto só na descrição" in r.motivo and loc in r.motivo, loc
    # cidade no título ("... - Austin") com localização vazia: campo vazio mantém comportamento antigo (passa)
    assert avaliar_palavras_chave(vaga(cargo="Software Engineer Intern - Austin", localizacao="",
                                       descricao="fully remote, python"), filtros).passou


def test_local_remoto_so_na_descricao_com_localizacao_generica_aceita(filtros):
    for loc in ("", "Brasil", "Brazil", "LATAM", "Worldwide"):
        r = avaliar_palavras_chave(vaga(cargo="Software Engineer Intern", localizacao=loc,
                                        descricao="100% remoto, python"), filtros)
        assert r.passou and "remoto" in r.motivo, loc
    # Maceió na localização + remote na descrição: passa
    assert avaliar_palavras_chave(vaga(cargo="Estágio Backend", localizacao="Maceió", descricao="remote ok, python"), filtros).passou
    # remoto no título ou na localização continua valendo
    assert avaliar_palavras_chave(vaga(cargo="Estágio Backend (remoto)", localizacao="Recife", descricao="python"), filtros).passou
    assert avaliar_palavras_chave(vaga(cargo="Estágio Backend", localizacao="Remote - Brazil", descricao="python"), filtros).passou
    assert avaliar_palavras_chave(vaga(cargo="Estágio Backend", localizacao="Maceió", descricao="python"), filtros).passou


def test_local_remoto_regiao_antes_ou_depois(filtros):
    for loc in ("Remote | Central US", "Remote - US", "Remoto - EUA", "Central US | Remote", "USA - Remote",
                "United States / Remote", "Remote (Canada)"):
        r = avaliar_palavras_chave(vaga(cargo="Software Engineer Intern", localizacao=loc, descricao="python"), filtros)
        assert not r.passou and "fora do Brasil" in r.motivo, loc
    for loc in ("Remote | Brazil", "Brazil - Remote", "Remote - Americas", "Remote (worldwide)", "Global - Remote", "LATAM | Remote"):
        assert avaliar_palavras_chave(vaga(cargo="Software Engineer Intern", localizacao=loc, descricao="python"), filtros).passou, loc


def test_filtro_palavra_inteira(filtros):
    # "jr" não casa em "major", "ml" não casa em "html"
    r = avaliar_palavras_chave(vaga(cargo="Major Accounts Backend", descricao="html remote"), filtros)
    assert not r.passou and "nível não identificado" in r.motivo


def test_filtro_bloqueios(filtros):
    casos = {
        "Machine Learning": "Estagiário em Machine Learning, remoto",
        "ML": "ML Engineer intern remote",
        "DS": "Data Scientist junior remote",
        "pytorch": "estágio remoto com PyTorch",
        "pandas": "estágio remoto, pandas e numpy",
        "vendas": "estágio remoto em vendas",
        "telemarketing": "júnior remoto telemarketing",
        "senior": "Senior backend, remote",
    }
    for nome, texto in casos.items():
        r = avaliar_palavras_chave(vaga(cargo="Vaga", descricao=texto), filtros)
        assert not r.passou, nome
    # "machine" sozinho não bloqueia
    assert avaliar_palavras_chave(vaga(descricao="estágio remoto; virtual machine e Python"), filtros).passou


def test_filtros_yaml_carrega_com_corte_padrao(filtros):
    assert filtros.nota_de_corte == 7
    assert "machine learning" in filtros.bloqueios
    assert "llm" in filtros.pontuam
