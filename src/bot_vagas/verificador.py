"""Verificador de fatos determinístico.

Rejeita qualquer afirmação do currículo adaptado que não exista no perfil:
1. IDs do plano devem existir no perfil (e sem repetição).
2. Datas/anos e números citados devem existir no perfil.
3. Termos próprios (palavras capitalizadas fora de início de frase, siglas, termos
   com cara de tecnologia como `Next.js`/`C#`) devem aparecer no corpus do perfil.
4. Lista de termos proibidos (tecnologias que o perfil não tem) em qualquer posição.

Uma camada com LLM pode ser injetada via `verificador_llm`, mas não há implementação real.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass

from .perfil import Idioma, Perfil
from .plano import PlanoAdaptacao

# Termos que o perfil NÃO contém e que o modelo costuma inventar (docs/DECISOES.md, "Escopo das vagas").
TERMOS_PROIBIDOS: tuple[str, ...] = (
    "machine learning",
    "aprendizado de máquina",
    "deep learning",
    "ml engineer",
    "data scientist",
    "cientista de dados",
    "pytorch",
    "tensorflow",
    "scikit-learn",
    "sklearn",
    "pandas",
    "numpy",
    "keras",
    "aws",
    "azure",
    "gcp",
    "kubernetes",
    "java",
    "c++",
    "c#",
    "go lang",
    "golang",
    "rust",
    "php",
    "django",
    "flask",
    "spring",
    "angular",
    "vue",
    "mongodb",
    "mysql",
    "redis",
    "graphql",
    "senior",
    "sênior",
    "pleno",
)

# Palavras capitalizadas comuns que não são fatos (pronome EN, siglas de seção etc.).
_IGNORAR = {"i", "i'm", "pt", "en"}

# Quebra de frase: pontuação seguida de espaço/fim, ou quebra de linha. Não quebra "Node.js".
_SEP_FRASE = re.compile(r"[.!?:;•](?=\s|$)|[\n\r]")
_TOKEN = re.compile(r"[\w][\w+#.'/-]*", re.UNICODE)
_DATA = re.compile(r"\b(?:\d{1,2}/)?(?:19|20)\d{2}\b")
_NUMERO = re.compile(r"\b\d+(?:[.,]\d+)?\b")


@dataclass(frozen=True)
class Violacao:
    tipo: str
    detalhe: str

    def __str__(self) -> str:
        return f"[{self.tipo}] {self.detalhe}"


VerificadorLLM = Callable[[str, Perfil, Idioma], list[Violacao]]
"""Interface opcional: (texto_curriculo, perfil, idioma) -> violações. Não implementada."""


def _normalizar(s: str) -> str:
    s = unicodedata.normalize("NFKC", s).lower()
    return s


def _tokens(texto: str) -> list[str]:
    return [t.strip(".'/-") for t in _TOKEN.findall(texto) if t.strip(".'/-")]


def _corpus(perfil: Perfil, idioma: Idioma) -> tuple[set[str], str]:
    textos = perfil.textos(idioma)
    toks = set()
    for t in textos:
        toks.update(_normalizar(x) for x in _tokens(t))
    # corpus plano para buscas de substrings (datas, números, bigramas)
    plano = _normalizar(" \n ".join(textos))
    return toks, plano


def _parece_termo_proprio(token: str, inicio_frase: bool) -> bool:
    if token.lower() in _IGNORAR:
        return False
    if any(ch in token for ch in "+#") or re.search(r"\w\.\w", token):
        return True  # C++, C#, Next.js, PL/pgSQL
    if token.isupper() and len(token) >= 2:
        return True  # siglas: SQL, AWS, ML
    if any(ch.isupper() for ch in token[1:]):
        return True  # CamelCase: TypeScript, GitHub
    if token[0].isupper() and not inicio_frase:
        return True
    return False


def verificar_texto(texto: str, perfil: Perfil, idioma: Idioma) -> list[Violacao]:
    """Confere um texto livre (ex.: resumo reescrito) contra o perfil."""
    violacoes: list[Violacao] = []
    toks_perfil, corpus = _corpus(perfil, idioma)
    texto_norm = _normalizar(texto)

    for termo in TERMOS_PROIBIDOS:
        if re.search(rf"(?<![\w]){re.escape(termo)}(?![\w])", texto_norm):
            violacoes.append(Violacao("termo_proibido", f"'{termo}' não existe no perfil"))

    for m in _DATA.finditer(texto):
        if m.group(0) not in corpus:
            violacoes.append(Violacao("data", f"data '{m.group(0)}' não existe no perfil"))

    numeros_perfil = set(_NUMERO.findall(corpus))
    for m in _NUMERO.finditer(texto):
        n = m.group(0)
        if _DATA.fullmatch(n):
            continue
        if n not in numeros_perfil:
            violacoes.append(Violacao("numero", f"número '{n}' não existe no perfil"))

    for frase in _SEP_FRASE.split(texto):
        toks = _tokens(frase)
        for i, tok in enumerate(toks):
            if _parece_termo_proprio(tok, inicio_frase=(i == 0)):
                if _normalizar(tok) not in toks_perfil:
                    violacoes.append(Violacao("termo_desconhecido", f"'{tok}' não existe no perfil"))

    # remove duplicatas mantendo ordem
    vistos: set[Violacao] = set()
    return [v for v in violacoes if not (v in vistos or vistos.add(v))]


def verificar_plano(plano: PlanoAdaptacao, perfil: Perfil) -> list[Violacao]:
    """Confere que o plano só referencia IDs existentes e sem repetição."""
    violacoes: list[Violacao] = []
    checagens = (
        ("ordem_competencias", plano.ordem_competencias, set(perfil.ids_tecnologias())),
        ("bullets_destaque", plano.bullets_destaque, set(perfil.ids_bullets())),
        ("ordem_projetos", plano.ordem_projetos, set(perfil.ids_projetos())),
    )
    for campo, ids, validos in checagens:
        for i in ids:
            if i not in validos:
                violacoes.append(Violacao("id_invalido", f"{campo}: id '{i}' não existe no perfil"))
        repetidos = sorted({i for i in ids if ids.count(i) > 1})
        for i in repetidos:
            violacoes.append(Violacao("id_repetido", f"{campo}: id '{i}' repetido"))
    return violacoes


def verificar(
    plano: PlanoAdaptacao,
    perfil: Perfil,
    texto_curriculo: str | None = None,
    verificador_llm: VerificadorLLM | None = None,
) -> list[Violacao]:
    """Verificação completa: plano + resumo + (opcional) texto final + (opcional) LLM.

    Retorna lista vazia quando tudo está de acordo com o perfil.
    """
    violacoes = verificar_plano(plano, perfil)
    violacoes += verificar_texto(plano.resumo, perfil, plano.idioma)
    if texto_curriculo is not None:
        for v in verificar_texto(texto_curriculo, perfil, plano.idioma):
            if v not in violacoes:
                violacoes.append(v)
    if verificador_llm is not None and texto_curriculo is not None:
        violacoes += verificador_llm(texto_curriculo, perfil, plano.idioma)
    return violacoes
