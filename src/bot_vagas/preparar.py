"""Etapa 4: PREPARA uma candidatura escolhida manualmente. Nada é enviado; sem login.

Fluxo (`preparar`):
1. salva a vaga no banco com fonte "manual" (dedupe pela chave existente; sem mudar o schema);
2. mostra o que o filtro por regra diria, só como AVISO (a escolha foi do Thiago);
3. detecta o idioma (pt/en) e escolhe a base do perfil;
4. sem `client` (sem --real): PDF NEUTRO, nenhuma chamada ao modelo;
   com `client` (--real): nota do avaliador + plano adaptado (plano_llm, que passa pelo
   verificador de fatos); se reprovar/recusar, cai no plano neutro com aviso. Cada chamada
   respeita o teto do contador de gasto e é registrada;
5. gera o PDF de 1 página em saida/<empresa>-<cargo>-<idioma>.pdf;
6. escreve o pacote saida/<empresa>-<cargo>.md com as respostas de respostas.yaml (só leitura
   local; nunca vai ao modelo) e marca a vaga como `preparada`.

A descrição da vaga é dado NÃO confiável: entra nos prompts só dentro do delimitador
`avaliador.delimitar_vaga`.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import parada
from .avaliador import Avaliacao, ErroAvaliacao, avaliar_vaga, delimitar_vaga
from .banco import Banco, chave_vaga
from .curriculo import CurriculoNaoCabeEmUmaPagina, gerar_curriculo
from .filtro import Filtros, avaliar_palavras_chave
from .fontes import Vaga
from .gasto import Contador, TetoAtingido
from .perfil import Idioma, Perfil
from .plano import PlanoAdaptacao, plano_neutro
from .plano_llm import ClienteMensagens, ErroPlano, PlanoInvalido, gerar_plano
from .respostas import MARCA_NULO, tabela_respostas

FONTE_MANUAL = "manual"


class VagaJaEnviada(RuntimeError):
    """Dedupe: a vaga já foi marcada como enviada; não repreparar sem pedido explícito."""

# Palavras frequentes que distinguem PT de EN (comparação por palavra inteira, sem acento).
_PT = ("de", "da", "do", "para", "com", "em", "não", "nao", "você", "voce", "nós", "nos", "uma", "um", "os", "as",
       "que", "estágio", "estagio", "vaga", "requisitos", "conhecimento", "experiência", "experiencia",
       "desenvolvimento", "atividades", "benefícios", "beneficios", "e", "ou", "será", "sera", "também", "tambem")
_EN = ("the", "and", "with", "for", "you", "we", "our", "is", "are", "will", "of", "to", "in", "on", "at", "this",
       "that", "internship", "intern", "requirements", "experience", "skills", "team", "work", "role", "as", "or",
       "an", "a", "be", "have", "about", "your", "develop")


def _norm(s: str) -> str:
    nfkd = unicodedata.normalize("NFKD", s or "")
    return "".join(c for c in nfkd if not unicodedata.combining(c)).lower()


def detectar_idioma(texto: str) -> Idioma:
    """Heurística simples por contagem de palavras funcionais. Empate ou vazio -> 'pt'."""
    toks = re.findall(r"[a-z]+", _norm(texto))
    pt = set(_norm(p) for p in _PT)
    en = set(_norm(p) for p in _EN)
    n_pt = sum(1 for t in toks if t in pt)
    n_en = sum(1 for t in toks if t in en)
    return "en" if n_en > n_pt else "pt"


def slug(texto: str, maximo: int = 40) -> str:
    s = _norm(texto)
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return (s[:maximo].rstrip("-")) or "sem-nome"


def nome_base(empresa: str, cargo: str) -> str:
    return f"{slug(empresa, 30)}-{slug(cargo, 40)}"


def vaga_manual(*, empresa: str, cargo: str, url: str, localizacao: str = "", descricao: str = "") -> Vaga:
    empresa = (empresa or "").strip() or "empresa-nao-informada"
    cargo = (cargo or "").strip() or "cargo-nao-informado"
    url = (url or "").strip() or f"manual:{hashlib.sha1((empresa + cargo + descricao).encode()).hexdigest()[:12]}"
    return Vaga(
        id_fonte=hashlib.sha1(url.encode("utf-8")).hexdigest()[:16],
        fonte=FONTE_MANUAL,
        empresa=empresa,
        cargo=cargo,
        localizacao=(localizacao or "").strip(),
        remoto=None,
        descricao=(descricao or "").strip(),
        url=url,
    )


@dataclass
class Resultado:
    chave: str
    vaga: Vaga
    idioma: Idioma
    ja_existia: bool
    filtro_diria: str
    plano_tipo: str  # "neutro" | "adaptado"
    pdf: Path | None = None
    pacote: Path | None = None
    avaliacao: Avaliacao | None = None
    avisos: list[str] = field(default_factory=list)
    chamadas: int = 0
    real: bool = False


class _Gravador:
    """Envolve o cliente para capturar cada resposta bruta (usage) sem mudar plano_llm."""

    def __init__(self, client: ClienteMensagens):
        self._client = client
        self.respostas: list[Any] = []
        from types import SimpleNamespace

        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        r = self._client.messages.create(**kwargs)
        self.respostas.append(r)
        return r


def _limpar_para_md(texto: str) -> str:
    """Texto da vaga/modelo dentro do .md: sem quebras de linha nem pipes (vira célula/linha)."""
    return re.sub(r"\s+", " ", (texto or "").replace("|", "¦")).strip()


def escrever_pacote(r: Resultado, respostas: dict[str, Any] | None, gasto_mes: float | None,
                    teto: float | None, destino: Path) -> Path:
    v = r.vaga
    linhas: list[str] = []
    linhas.append(f"# Pacote de candidatura — {v.empresa} / {v.cargo}")
    linhas.append("")
    linhas.append("> Preparado pelo bot-vagas. NADA foi enviado: você abre o link, anexa o PDF e cola as respostas.")
    linhas.append("> Este arquivo contém dados pessoais (respostas.yaml) e fica só neste computador.")
    linhas.append("")
    linhas.append("## Vaga")
    linhas.append(f"- Link: {v.url}")
    linhas.append(f"- Empresa: {_limpar_para_md(v.empresa)}")
    linhas.append(f"- Cargo: {_limpar_para_md(v.cargo)}")
    linhas.append(f"- Localização: {_limpar_para_md(v.localizacao) or 'não informada'}")
    linhas.append(f"- Idioma detectado: {r.idioma}")
    linhas.append(f"- O filtro por regra diria: {r.filtro_diria} (apenas aviso; a escolha foi sua)")
    linhas.append("")
    linhas.append("## Avaliação")
    if r.avaliacao:
        linhas.append(f"- Nota: {r.avaliacao.nota}/10")
        linhas.append(f"- Motivo: {_limpar_para_md(r.avaliacao.motivo)}")
        linhas.append("- Pontos de match:")
        linhas += [f"  - {_limpar_para_md(p)}" for p in r.avaliacao.pontos_de_match] or ["  - (nenhum)"]
        linhas.append("- Requisitos que faltam:")
        linhas += [f"  - {_limpar_para_md(p)}" for p in r.avaliacao.requisitos_que_faltam] or ["  - (nenhum)"]
    elif r.real:
        linhas.append("- Nota: não obtida (veja avisos).")
    else:
        linhas.append("- Nota: não calculada (rodado SEM --real; nenhuma chamada ao modelo).")
        linhas.append("- Com --real o bot pediria ao modelo a nota 0-10, os pontos de match/faltas e um plano de")
        linhas.append("  adaptação do currículo, verificado contra o perfil antes de gerar o PDF.")
    linhas.append("")
    linhas.append("## Currículo")
    linhas.append(f"- PDF ({'adaptado à vaga' if r.plano_tipo == 'adaptado' else 'NEUTRO, base do perfil'}): {r.pdf}")
    linhas.append("- Confira pessoalmente: nome, e-mail, telefone, links e se o conteúdo faz sentido para a vaga.")
    linhas.append("")
    if r.avisos:
        linhas.append("## Avisos")
        linhas += [f"- {a}" for a in r.avisos]
        linhas.append("")
    linhas.append("## Respostas prontas (de respostas.yaml; nenhum modelo envolvido)")
    linhas.append("")
    linhas.append(f"Campos marcados **{MARCA_NULO}** estão vazios no respostas.yaml: responda você mesmo.")
    linhas.append("Pretensão salarial: informe SÓ se o formulário perguntar.")
    linhas.append("")
    if respostas is None:
        linhas.append("ATENÇÃO: respostas.yaml não foi encontrado; todos os campos abaixo precisam ser preenchidos por você.")
        linhas.append("")
    linhas.append("| Campo | Resposta |")
    linhas.append("|---|---|")
    for rotulo, valor, nulo in tabela_respostas(respostas):
        valor_md = f"**{valor}**" if nulo else _limpar_para_md(valor)
        linhas.append(f"| {rotulo} | {valor_md} |")
    linhas.append("")
    linhas.append("## Gasto")
    if gasto_mes is None:
        linhas.append("- Gasto do mês: não disponível.")
    else:
        linhas.append(f"- Chamadas ao modelo nesta preparação: {r.chamadas}")
        linhas.append(f"- Gasto do mês até agora: US$ {gasto_mes:.4f}" + (f" (teto US$ {teto:.2f})" if teto else ""))
    linhas.append("")
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text("\n".join(linhas), encoding="utf-8")
    return destino


def preparar(
    vaga: Vaga,
    *,
    perfil: Perfil,
    banco: Banco,
    filtros: Filtros,
    respostas: dict[str, Any] | None,
    saida: Path,
    client: ClienteMensagens | None = None,
    contador: Contador | None = None,
    idioma: Idioma | None = None,
    log=print,
    permitir_enviada: bool = False,
) -> Resultado:
    """`client` presente == modo --real. Sem ele, nenhuma função que fala com o modelo é chamada.
    Vaga já `enviada` (dedupe) só é preparada de novo com `permitir_enviada=True` (--forcar na CLI)."""
    saida = Path(saida)
    chave = chave_vaga(vaga.empresa, vaga.cargo, vaga.url)
    existente = banco.buscar_por_chave(chave)
    ja_existia = existente is not None
    if not ja_existia:
        banco.salvar_vagas([vaga])
    else:
        if existente["status"] == "enviada" and not permitir_enviada:
            raise VagaJaEnviada(
                f"esta vaga já está marcada como ENVIADA (chave {chave[:10]}…). Não vou preparar de novo; "
                "se quiser mesmo, repita com --forcar."
            )
        log(f"aviso: vaga já estava no banco (chave {chave[:10]}…); vou reaproveitar o registro.")

    rf = avaliar_palavras_chave(vaga, filtros)
    filtro_diria = "passaria" if rf.passou else f"bloqueada por: {rf.motivo}"
    log(f"o filtro diria: {filtro_diria}  [aviso apenas; a vaga foi escolhida por você]")

    idioma_final: Idioma = idioma or detectar_idioma(f"{vaga.cargo}\n{vaga.descricao}")
    log(f"idioma da vaga: {idioma_final} -> base {idioma_final.upper()} do perfil")

    r = Resultado(chave=chave, vaga=vaga, idioma=idioma_final, ja_existia=ja_existia,
                  filtro_diria=filtro_diria, plano_tipo="neutro", real=client is not None)
    plano: PlanoAdaptacao = plano_neutro(perfil, idioma_final)
    detalhes = _detalhes_atuais(banco, chave)

    if client is None:
        r.avisos.append("rodado SEM --real: nenhuma chamada ao modelo; PDF neutro (base do perfil).")
        log("sem --real: não vou chamar a API. Com --real eu pediria nota 0-10 e um plano de adaptação "
            "verificado contra o perfil.")
    else:
        if contador is None:
            raise ValueError("modo --real exige o contador de gasto")
        gravador = _Gravador(client)
        # (i) nota
        parada.verificar()  # botão PARAR antes de cada chamada paga
        try:
            contador.exigir_orcamento()
            av, resposta = avaliar_vaga(perfil, vaga, gravador)
            contador.registrar(resposta, finalidade="avaliar", chave_vaga=chave)
            r.chamadas += 1
            r.avaliacao = av
            detalhes["avaliacao"] = av.model_dump()
            log(f"nota do modelo: {av.nota}/10 — {av.motivo}")
        except TetoAtingido as e:
            r.avisos.append(f"nota não obtida: {e}")
            log(f"AVISO: {e}", file=sys.stderr)
        except ErroAvaliacao as e:
            if gravador.respostas:
                contador.registrar(gravador.respostas[-1], finalidade="avaliar", chave_vaga=chave)
                r.chamadas += 1
            r.avisos.append(f"nota não obtida: {e}")
            log(f"AVISO: avaliação falhou: {e}", file=sys.stderr)
        # (ii) plano adaptado, verificado
        n_antes = len(gravador.respostas)
        parada.verificar()
        try:
            contador.exigir_orcamento()
            plano = gerar_plano(perfil, delimitar_vaga(vaga), idioma_final, gravador)
            r.plano_tipo = "adaptado"
            log("plano de adaptação gerado e aprovado pelo verificador de fatos")
        except TetoAtingido as e:
            r.avisos.append(f"plano adaptado não gerado: {e}; usei o plano neutro")
            log(f"AVISO: {e}", file=sys.stderr)
        except ErroPlano as e:
            motivo = "reprovado pelo verificador de fatos" if isinstance(e, PlanoInvalido) and e.violacoes else str(e)
            r.avisos.append(f"plano adaptado descartado ({motivo}); usei o plano neutro")
            log(f"AVISO: {e}\n-> caindo no plano neutro.", file=sys.stderr)
            plano = plano_neutro(perfil, idioma_final)
        finally:
            for resp in gravador.respostas[n_antes:]:
                contador.registrar(resp, finalidade="plano", chave_vaga=chave)
                r.chamadas += 1

    # (iii) PDF de exatamente 1 página
    base = nome_base(vaga.empresa, vaga.cargo)
    destino_pdf = saida / f"{base}-{idioma_final}.pdf"
    try:
        gerar_curriculo(perfil, plano, destino_pdf)
    except CurriculoNaoCabeEmUmaPagina as e:
        if r.plano_tipo == "adaptado":
            log(f"ERRO: o currículo adaptado não coube em 1 página ({e}); gerando o neutro no lugar.", file=sys.stderr)
            r.avisos.append(f"currículo adaptado NÃO coube em 1 página ({e.paginas} páginas); PDF neutro gerado no lugar")
            r.plano_tipo = "neutro"
            gerar_curriculo(perfil, plano_neutro(perfil, idioma_final), destino_pdf)
        else:
            raise
    r.pdf = destino_pdf
    log(f"PDF ({r.plano_tipo}, {idioma_final}): {destino_pdf}")

    # pacote .md (respostas.yaml só aqui)
    gasto = contador.gasto_do_mes() if contador else None
    teto = contador.config.teto_mensal_usd if contador else None
    r.pacote = escrever_pacote(r, respostas, gasto, teto, saida / f"{base}.md")
    log(f"pacote: {r.pacote}")

    # status preparada (sem mudar o schema: tudo em detalhes JSON)
    detalhes["preparacao"] = {
        "pdf": str(destino_pdf), "pacote": str(r.pacote), "idioma": idioma_final, "plano": r.plano_tipo,
        "filtro_diria": filtro_diria, "real": r.real, "avisos": r.avisos, "chamadas": r.chamadas,
    }
    motivo = r.avaliacao.motivo if r.avaliacao else f"preparada manualmente; filtro diria: {filtro_diria}"
    banco.atualizar_status(chave, "preparada", nota=float(r.avaliacao.nota) if r.avaliacao else None,
                           motivo=motivo, detalhes=json.dumps(detalhes, ensure_ascii=False))
    return r


def _detalhes_atuais(banco: Banco, chave: str) -> dict[str, Any]:
    row = banco.buscar_por_chave(chave)
    if row is None or not row["detalhes"]:
        return {}
    try:
        d = json.loads(row["detalhes"])
    except ValueError:
        return {}
    return d if isinstance(d, dict) else {}
