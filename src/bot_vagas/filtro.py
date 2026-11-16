"""Fase A do filtro: palavras-chave configuráveis (filtros.yaml).

A elegibilidade de localização (remoto ou presencial/híbrido em Maceió) e de nível é decidida
AQUI, por regra; o modelo (fase B) nunca sobrepõe esta decisão.

Regra de nível (seção `nivel` do filtros.yaml), SÓ NO TÍTULO:
  - por ora só estágio/trainee são aceitos (júnior está excluído; ver comentários no yaml);
  - se o título tem um termo de `nivel.aceitos`, a vaga passa de nível, mesmo que também tenha
    um termo de `nivel.bloqueiam_no_titulo` ("Estágio Dev Jr" aceita: estágio é o que se quer);
  - senão, se o título tem termo de `bloqueiam_no_titulo` bloqueia ("nível não aceito");
  - senão bloqueia ("nível não identificado"). A descrição NÃO é consultada para nível.

Regra de localização (seção `localizacao_aceita` do filtros.yaml):
  - aceita se a vaga é remota (fonte informa remoto=True, ou termo de `remoto.termos` no TÍTULO
    ou na LOCALIZAÇÃO) e NÃO há restrição geográfica clara fora do Brasil
    (`remoto.restricoes_fora_do_brasil` + heurística "remote - US" / "Central US | Remote");
  - termo de remoto SÓ na descrição vale apenas se o campo localização está vazio, indica
    Maceió/AL ou indica uma região ampla (`localizacao_aceita.regioes_amplas`: Brasil, LATAM,
    worldwide...). Se a localização é uma cidade específica ("Austin, TX", "London"), um
    "remote" solto na descrição não torna a vaga remota;
  - aceita se a vaga é presencial/híbrida (fonte informa remoto=False, ou termo de
    `localizacao_aceita.termos_presencial` no título/localização) E a localização indica Maceió/AL;
  - caso contrário bloqueia ("presencial fora de Maceió" / "remoto não identificado").
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .fontes import Vaga
from .fontes.texto import normalizar
from .perfil import RAIZ_PROJETO

FILTROS_PADRAO = RAIZ_PROJETO / "filtros.yaml"

# Heurística para "remote - US only", "remote (USA)", "remoto: Portugal", "Remote | Central US",
# "Central US | Remote", "Remoto - EUA": remoto e uma região fora do Brasil separados por
# | – - ( ) / : , em qualquer ordem. Complementa a lista `remoto.restricoes_fora_do_brasil`.
# "Brazil", "LATAM", "Americas", "worldwide", "global" NÃO estão aqui e continuam aceitos.
_REGIOES_FORA = (
    r"(?:(?:central|east|west|eastern|western|north|south|northern|southern)\s+)?"
    r"(?:us|usa|u\.s\.|united states|eua|estados unidos|uk|eu|europe|europa|canada|portugal|latam ex brazil)"
)
_SEP = r"\s*[-–()/:,|]\s*"
_REMOTO = r"remot[eoa]\w*"
_PADRAO_REMOTO_RESTRITO = re.compile(
    rf"(?:{_REMOTO}{_SEP}{_REGIOES_FORA}(?![a-z0-9]))"
    rf"|(?:(?<![a-z0-9]){_REGIOES_FORA}\)?{_SEP}{_REMOTO})"
)


@dataclass(frozen=True)
class Filtros:
    nota_de_corte: float = 7.0
    termos_remoto: tuple[str, ...] = ()
    restricoes_fora_do_brasil: tuple[str, ...] = ()
    termos_presencial: tuple[str, ...] = ()
    cidades_presencial: tuple[str, ...] = ()
    estados_siglas: tuple[str, ...] = ()
    estados_nomes: tuple[str, ...] = ()
    regioes_amplas: tuple[str, ...] = ()
    niveis_aceitos: tuple[str, ...] = ()
    niveis_bloqueiam_titulo: tuple[str, ...] = ()
    bloqueios: tuple[str, ...] = ()
    pontuam: tuple[str, ...] = ()


@dataclass
class ResultadoFiltro:
    passou: bool
    motivo: str
    pontos: int = 0
    termos_encontrados: list[str] = field(default_factory=list)


def _tupla(valores) -> tuple[str, ...]:
    return tuple(normalizar(str(v)) for v in (valores or []) if str(v).strip())


def carregar_filtros(caminho: Path | str = FILTROS_PADRAO) -> Filtros:
    with open(caminho, encoding="utf-8") as f:
        d = yaml.safe_load(f) or {}
    if not isinstance(d, dict):
        raise ValueError(f"filtros.yaml inválido em {caminho}")
    remoto = d.get("remoto") or {}
    local = d.get("localizacao_aceita") or {}
    nivel = d.get("nivel") or {}
    return Filtros(
        nota_de_corte=float(d.get("nota_de_corte", 7)),
        termos_remoto=_tupla(remoto.get("termos")),
        restricoes_fora_do_brasil=_tupla(remoto.get("restricoes_fora_do_brasil")),
        termos_presencial=_tupla(local.get("termos_presencial")),
        cidades_presencial=_tupla(local.get("cidades_presencial")),
        estados_siglas=_tupla(local.get("estados_siglas")),
        estados_nomes=_tupla(local.get("estados_nomes")),
        regioes_amplas=_tupla(local.get("regioes_amplas")),
        niveis_aceitos=_tupla(nivel.get("aceitos")),
        niveis_bloqueiam_titulo=_tupla(nivel.get("bloqueiam_no_titulo")),
        bloqueios=_tupla(d.get("bloqueios")),
        pontuam=_tupla(d.get("pontuam")),
    )


def _contem(texto_norm: str, termo: str) -> bool:
    """Casa o termo como palavra(s) inteira(s): 'jr' não casa em 'major', 'ml' não em 'html'."""
    padrao = r"(?<![a-z0-9])" + re.escape(termo) + r"(?![a-z0-9])"
    return re.search(padrao, texto_norm) is not None


def _achados(texto_norm: str, termos: tuple[str, ...]) -> list[str]:
    return [t for t in termos if _contem(texto_norm, t)]


def _local_em_maceio(vaga: Vaga, filtros: Filtros) -> list[str]:
    """Localização aceita para presencial/híbrido.

    Cidade: título ou localização; a descrição é apoio fraco, usada só quando o campo
    localização está vazio (se ele diz "Recife", um "Maceió" solto na descrição não salva).
    Sigla/nome do estado: só no campo localização, porque "al" solto na descrição é ambíguo.
    """
    cabecalho = normalizar(f"{vaga.cargo} {vaga.localizacao}")
    local = normalizar(vaga.localizacao)
    achados = _achados(cabecalho, filtros.cidades_presencial)
    achados += _achados(local, filtros.estados_siglas + filtros.estados_nomes)
    if not achados and not local.strip():
        achados = _achados(normalizar(vaga.descricao), filtros.cidades_presencial)
    return achados


def _restricao_fora_do_brasil(tudo: str, filtros: Filtros) -> str | None:
    achados = _achados(tudo, filtros.restricoes_fora_do_brasil)
    if achados:
        return achados[0]
    m = _PADRAO_REMOTO_RESTRITO.search(tudo)
    return m.group(0) if m else None


def avaliar_palavras_chave(vaga: Vaga, filtros: Filtros) -> ResultadoFiltro:
    titulo = normalizar(vaga.cargo)
    cabecalho = normalizar(f"{vaga.cargo} {vaga.localizacao}")
    tudo = normalizar(f"{vaga.cargo}\n{vaga.localizacao}\n{vaga.descricao}")

    # 1) bloqueios (qualquer campo)
    bloq = _achados(tudo, filtros.bloqueios)
    if bloq:
        return ResultadoFiltro(False, f"bloqueio: {', '.join(bloq)}", termos_encontrados=bloq)

    # 2) nível, SÓ NO TÍTULO. Termo aceito (estágio/trainee) no título vence o bloqueio:
    #    "Estágio Dev Jr" aceita. Sem termo aceito, termo bloqueado ("junior", "senior"...)
    #    bloqueia; sem nenhum dos dois, "nível não identificado". Descrição não é consultada.
    nivel_ok = _achados(titulo, filtros.niveis_aceitos)
    if filtros.niveis_aceitos and not nivel_ok:
        nivel_ruim = _achados(titulo, filtros.niveis_bloqueiam_titulo)
        if nivel_ruim:
            return ResultadoFiltro(False, f"nível não aceito no título: {', '.join(nivel_ruim)}", termos_encontrados=nivel_ruim)
        return ResultadoFiltro(False, "nível não identificado no título (estágio/trainee)")

    # 3) localização: remoto OU presencial/híbrido em Maceió
    # Presencial/híbrido só conta quando a FONTE diz que não é remoto ou o termo está no
    # título/localização; "não é presencial" na descrição de uma vaga remota não bloqueia.
    presencial = _achados(cabecalho, filtros.termos_presencial)
    eh_presencial = vaga.remoto is False or bool(presencial)
    # Remoto: fonte explícita, ou termo no título/localização. Termo SÓ na descrição vale
    # apenas se o campo localização está vazio ou não aponta para um lugar específico fora de
    # Maceió (ex.: "Brasil", "LATAM", "worldwide" liberam; "Austin, TX" ou "London" não).
    local = normalizar(vaga.localizacao)
    eh_remoto = vaga.remoto is True or bool(_achados(cabecalho, filtros.termos_remoto))
    remoto_so_na_descricao = False
    if not eh_remoto and _achados(normalizar(vaga.descricao), filtros.termos_remoto):
        local_generico = (
            not local.strip()
            or bool(_local_em_maceio(vaga, filtros))
            or bool(_achados(local, filtros.regioes_amplas))
        )
        if local_generico:
            eh_remoto = True
        else:
            remoto_so_na_descricao = True

    if eh_presencial:
        local_ok = _local_em_maceio(vaga, filtros)
        if not local_ok:
            origem = "fonte informa que não é remoto" if vaga.remoto is False else ", ".join(presencial)
            return ResultadoFiltro(False, f"presencial fora de Maceió ({origem})", termos_encontrados=presencial)
        modo = f"presencial/híbrido em Maceió: {', '.join(local_ok)}"
    elif eh_remoto:
        restricao = _restricao_fora_do_brasil(tudo, filtros)
        if restricao:
            return ResultadoFiltro(False, f"remoto restrito fora do Brasil: {restricao}", termos_encontrados=[restricao])
        modo = "remoto"
    else:
        local_ok = _local_em_maceio(vaga, filtros)
        if local_ok:
            modo = f"localização em Maceió: {', '.join(local_ok)}"
        elif remoto_so_na_descricao:
            return ResultadoFiltro(
                False,
                f"remoto só na descrição; localização '{vaga.localizacao.strip()}' é um lugar específico fora de Maceió",
            )
        else:
            return ResultadoFiltro(False, "remoto não identificado e localização fora de Maceió")

    pont = _achados(tudo, filtros.pontuam)
    return ResultadoFiltro(
        True,
        f"passou ({modo}; nível: {', '.join(nivel_ok) or 'n/a'}; +{len(pont)} termos)",
        pontos=len(pont),
        termos_encontrados=pont,
    )
