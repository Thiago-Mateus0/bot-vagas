"""Orquestra as três fases (buscar -> filtrar -> avaliar) sobre o SQLite.

Nenhuma função aqui cria cliente HTTP nem cliente da Anthropic: ambos são injetados pela CLI.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from typing import Any

from .avaliador import ErroAvaliacao, avaliar_vaga
from .banco import STATUS, Banco
from .filtro import Filtros, avaliar_palavras_chave
from .fontes import Fonte, Vaga
from .fontes.http import ClienteHTTP, ErroHTTP
from .gasto import Contador, TetoAtingido
from .parada import exigir_nao_parado
from .perfil import Perfil
from .plano_llm import ClienteMensagens


def _parse_data(valor: str | None) -> datetime | None:
    if not valor:
        return None
    try:
        return datetime.fromisoformat(valor)
    except ValueError:
        return None


def vaga_de_linha(row) -> Vaga:
    return Vaga(
        id_fonte=row["id_fonte"],
        fonte=row["fonte"],
        empresa=row["empresa"],
        cargo=row["cargo"],
        localizacao=row["localizacao"] or "",
        remoto=None if row["remoto"] is None else bool(row["remoto"]),
        descricao=row["descricao"] or "",
        url=row["url"],
        data=_parse_data(row["data_vaga"]),
    )


def buscar(fontes: list[Fonte], http: ClienteHTTP, banco: Banco, log=print) -> dict[str, int]:
    """Entre uma fonte e outra, respeita o arquivo PARAR (ParadaSolicitada); o que já foi salvo fica salvo.
    `alarmes` conta avisos de mudança de formato/bloqueio (hoje só a Gupy os emite, em
    `fonte.alarmes`); a CLI devolve código de saída 1 quando há algum."""
    total = {"baixadas": 0, "novas": 0, "fontes_com_erro": 0, "alarmes": 0}
    for fonte in fontes:
        exigir_nao_parado()  # botão PARAR: levanta ParadaSolicitada
        rotulo = f"{fonte.nome}:{getattr(fonte, 'token', None) or getattr(fonte, 'empresa', None) or ''}"
        try:
            vagas = fonte.buscar(http)
        except ErroHTTP as e:
            total["fontes_com_erro"] += 1
            log(f"[{rotulo}] erro: {e}", file=sys.stderr)
            continue
        for alarme in getattr(fonte, "alarmes", None) or []:
            total["alarmes"] += 1
            log(f"[{rotulo}] ALARME: {alarme}", file=sys.stderr)
        novas = banco.salvar_vagas(vagas)
        total["baixadas"] += len(vagas)
        total["novas"] += novas
        log(f"[{rotulo}] {len(vagas)} vagas, {novas} novas")
    return total


def filtrar(banco: Banco, filtros: Filtros, log=print) -> dict[str, int]:
    """Aplica a fase A às vagas com status 'nova'.

    Quem não passa vira 'bloqueada' (com o motivo). Quem passa continua 'nova', com
    detalhes.filtro='passou', aguardando a fase B (que a leva a 'aprovada' ou 'abaixo_do_corte').
    Vagas já filtradas não são refiltradas."""
    total = {"passaram": 0, "bloqueadas": 0}
    for row in banco.listar("nova"):
        exigir_nao_parado()  # botão PARAR: levanta ParadaSolicitada
        detalhes = json.loads(row["detalhes"]) if row["detalhes"] else {}
        if detalhes.get("filtro") == "passou":
            continue  # já filtrada antes
        vaga = vaga_de_linha(row)
        r = avaliar_palavras_chave(vaga, filtros)
        if r.passou:
            detalhes.update(filtro="passou", pontos=r.pontos, termos=r.termos_encontrados)
            banco.atualizar_status(row["chave"], "nova", motivo=r.motivo, detalhes=json.dumps(detalhes, ensure_ascii=False))
            total["passaram"] += 1
        else:
            detalhes.update(filtro="bloqueada")
            banco.atualizar_status(row["chave"], "bloqueada", motivo=r.motivo, detalhes=json.dumps(detalhes, ensure_ascii=False))
            total["bloqueadas"] += 1
    log(f"filtro: {total['passaram']} passaram, {total['bloqueadas']} bloqueadas")
    return total


def refiltrar(banco: Banco, filtros: Filtros, log=print) -> dict[str, int]:
    """Reaplica a fase A a TODAS as vagas 'nova' e 'bloqueada' que ainda não têm nota.

    Usado quando as regras do filtro mudam. Vagas com nota do modelo (aprovada,
    abaixo_do_corte, avaliada ou qualquer uma com `nota` preenchida) nunca são tocadas:
    reavaliá-las custaria dinheiro. Elas são apenas contadas em 'ignoradas_com_nota'.
    Não faz chamadas ao modelo nem à rede."""
    total = {"bloqueada_para_nova": 0, "nova_para_bloqueada": 0, "iguais": 0, "ignoradas_com_nota": 0}
    for row in banco.listar(STATUS):
        if row["nota"] is not None or row["status"] not in ("nova", "bloqueada"):
            total["ignoradas_com_nota"] += 1
            continue
        detalhes = json.loads(row["detalhes"]) if row["detalhes"] else {}
        vaga = vaga_de_linha(row)
        r = avaliar_palavras_chave(vaga, filtros)
        antes = row["status"]
        if r.passou:
            detalhes.update(filtro="passou", pontos=r.pontos, termos=r.termos_encontrados)
            depois = "nova"
        else:
            detalhes.update(filtro="bloqueada")
            detalhes.pop("pontos", None)
            detalhes.pop("termos", None)
            depois = "bloqueada"
        banco.atualizar_status(row["chave"], depois, motivo=r.motivo, detalhes=json.dumps(detalhes, ensure_ascii=False))
        if antes == depois:
            total["iguais"] += 1
        elif antes == "bloqueada":
            total["bloqueada_para_nova"] += 1
        else:
            total["nova_para_bloqueada"] += 1
    log(f"refiltro: {total['bloqueada_para_nova']} bloqueada->nova, {total['nova_para_bloqueada']} nova->bloqueada, "
        f"{total['iguais']} permaneceram iguais, {total['ignoradas_com_nota']} ignoradas por já terem nota do modelo")
    return total


def pendentes_de_avaliacao(banco: Banco) -> list[Any]:
    out = []
    for row in banco.listar("nova"):
        detalhes = json.loads(row["detalhes"]) if row["detalhes"] else {}
        if detalhes.get("filtro") == "passou":
            out.append(row)
    return out


def avaliar(perfil: Perfil, banco: Banco, filtros: Filtros, client: ClienteMensagens,
            contador: Contador, *, limite: int | None = None, log=print) -> dict[str, int]:
    """Fase B: só para vagas que passaram na fase A. Para ao atingir o teto."""
    total = {"aprovadas": 0, "abaixo_do_corte": 0, "erros": 0, "bloqueadas_por_teto": 0}
    pendentes = pendentes_de_avaliacao(banco)
    if limite is not None:
        pendentes = pendentes[:limite]
    for row in pendentes:
        exigir_nao_parado()  # botão PARAR: levanta ParadaSolicitada
        try:
            contador.exigir_orcamento()
        except TetoAtingido as e:
            log(f"AVISO: {e}", file=sys.stderr)
            total["bloqueadas_por_teto"] = len(pendentes) - total["aprovadas"] - total["abaixo_do_corte"] - total["erros"]
            break
        vaga = vaga_de_linha(row)
        try:
            av, resposta = avaliar_vaga(perfil, vaga, client)
        except ErroAvaliacao as e:
            total["erros"] += 1
            log(f"[{vaga.empresa} / {vaga.cargo}] erro na avaliação: {e}", file=sys.stderr)
            continue
        contador.registrar(resposta, finalidade="avaliar", chave_vaga=row["chave"])
        detalhes = json.loads(row["detalhes"]) if row["detalhes"] else {}
        detalhes.update(avaliacao=av.model_dump())
        status = "aprovada" if av.nota >= filtros.nota_de_corte else "abaixo_do_corte"
        banco.atualizar_status(row["chave"], status, nota=float(av.nota), motivo=av.motivo,
                               detalhes=json.dumps(detalhes, ensure_ascii=False))
        total[status.replace("aprovada", "aprovadas")] += 1
        log(f"[{vaga.empresa} / {vaga.cargo}] nota {av.nota} -> {status}")
    return total
