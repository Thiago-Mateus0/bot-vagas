"""Modelos pydantic do perfil (fonte única de verdade do currículo) e carregamento do YAML."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

Idioma = Literal["pt", "en"]

RAIZ_PROJETO = Path(__file__).resolve().parents[2]
# BOT_VAGAS_PERFIL existe só para os testes (perfil.example.yaml num clone sem dados pessoais).
# Em uso normal NÃO defina: sem perfil.yaml o bot falha em vez de gerar currículo com dados falsos.
PERFIL_PADRAO = Path(os.environ.get("BOT_VAGAS_PERFIL") or RAIZ_PROJETO / "perfil.yaml")


class _Modelo(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Texto(_Modelo):
    """Texto bilíngue."""

    pt: str = Field(min_length=1)
    en: str = Field(min_length=1)

    def em(self, idioma: Idioma) -> str:
        return getattr(self, idioma)


TextoOuStr = Texto | str


def texto_em(valor: TextoOuStr, idioma: Idioma) -> str:
    return valor if isinstance(valor, str) else valor.em(idioma)


class Contato(_Modelo):
    cidade: Texto
    email: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    telefone: str = Field(min_length=1)
    linkedin: str = Field(min_length=1)
    github: str = Field(min_length=1)


class Bullet(_Modelo):
    id: str = Field(pattern=r"^[a-z0-9_]+$")
    texto: Texto


class HabilidadeInterpessoal(_Modelo):
    id: str = Field(pattern=r"^[a-z0-9_]+$")
    titulo: Texto
    texto: Texto


class Educacao(_Modelo):
    id: str = Field(pattern=r"^[a-z0-9_]+$")
    curso: Texto
    instituicao: str = Field(min_length=1)
    local: Texto
    periodo: Texto
    bullets: list[Bullet] = Field(default_factory=list)


class Tecnologia(_Modelo):
    id: str = Field(pattern=r"^[a-z0-9_]+$")
    nome: Texto


class IdiomaFalado(_Modelo):
    id: str = Field(pattern=r"^[a-z0-9_]+$")
    nome: Texto
    nivel: Texto


class Competencias(_Modelo):
    tecnologias: list[Tecnologia] = Field(min_length=1)
    idiomas: list[IdiomaFalado] = Field(min_length=1)


class Experiencia(_Modelo):
    id: str = Field(pattern=r"^[a-z0-9_]+$")
    empresa: str = Field(min_length=1)
    cargo: Texto
    local: Texto
    periodo: Texto
    bullets: list[Bullet] = Field(min_length=1)


class Projeto(_Modelo):
    id: str = Field(pattern=r"^[a-z0-9_]+$")
    nome: TextoOuStr
    descricao: Texto


class Secoes(_Modelo):
    sobre: Texto
    interpessoais: Texto
    educacao: Texto
    competencias: Texto
    experiencia: Texto
    projetos: Texto
    idiomas: Texto


class Perfil(_Modelo):
    nome: str = Field(min_length=1)
    titulo: Texto
    contato: Contato
    resumo: Texto
    habilidades_interpessoais: list[HabilidadeInterpessoal] = Field(min_length=1)
    educacao: list[Educacao] = Field(min_length=1)
    competencias: Competencias
    experiencia: list[Experiencia] = Field(min_length=1)
    projetos: list[Projeto] = Field(min_length=1)
    secoes: Secoes

    @model_validator(mode="after")
    def _ids_unicos(self) -> "Perfil":
        ids = self.todos_ids()
        duplicados = sorted({i for i in ids if ids.count(i) > 1})
        if duplicados:
            raise ValueError(f"IDs duplicados no perfil: {duplicados}")
        return self

    # --- índices úteis -------------------------------------------------

    def todos_ids(self) -> list[str]:
        ids: list[str] = []
        ids += [h.id for h in self.habilidades_interpessoais]
        for e in self.educacao:
            ids.append(e.id)
            ids += [b.id for b in e.bullets]
        ids += [t.id for t in self.competencias.tecnologias]
        ids += [i.id for i in self.competencias.idiomas]
        for x in self.experiencia:
            ids.append(x.id)
            ids += [b.id for b in x.bullets]
        ids += [p.id for p in self.projetos]
        return ids

    def ids_tecnologias(self) -> list[str]:
        return [t.id for t in self.competencias.tecnologias]

    def ids_bullets(self) -> list[str]:
        ids: list[str] = []
        for e in self.educacao:
            ids += [b.id for b in e.bullets]
        for x in self.experiencia:
            ids += [b.id for b in x.bullets]
        return ids

    def ids_projetos(self) -> list[str]:
        return [p.id for p in self.projetos]

    def ids_habilidades(self) -> list[str]:
        return [h.id for h in self.habilidades_interpessoais]

    def textos(self, idioma: Idioma) -> list[str]:
        """Todos os textos do perfil num idioma (corpus para o verificador)."""
        c = self.contato
        out: list[str] = [
            self.nome,
            self.titulo.em(idioma),
            c.cidade.em(idioma),
            c.email,
            c.telefone,
            c.linkedin,
            c.github,
            self.resumo.em(idioma),
        ]
        for h in self.habilidades_interpessoais:
            out += [h.titulo.em(idioma), h.texto.em(idioma)]
        for e in self.educacao:
            out += [e.curso.em(idioma), e.instituicao, e.local.em(idioma), e.periodo.em(idioma)]
            out += [b.texto.em(idioma) for b in e.bullets]
        out += [t.nome.em(idioma) for t in self.competencias.tecnologias]
        for i in self.competencias.idiomas:
            out += [i.nome.em(idioma), i.nivel.em(idioma)]
        for x in self.experiencia:
            out += [x.empresa, x.cargo.em(idioma), x.local.em(idioma), x.periodo.em(idioma)]
            out += [b.texto.em(idioma) for b in x.bullets]
        for p in self.projetos:
            out += [texto_em(p.nome, idioma), p.descricao.em(idioma)]
        s = self.secoes
        out += [getattr(s, campo).em(idioma) for campo in Secoes.model_fields]
        return out


def carregar_perfil(caminho: Path | str = PERFIL_PADRAO) -> Perfil:
    """Lê e valida o perfil YAML. Usa yaml.safe_load (nunca executa conteúdo)."""
    with open(caminho, encoding="utf-8") as f:
        dados = yaml.safe_load(f)
    if not isinstance(dados, dict):
        raise ValueError(f"perfil inválido em {caminho}: esperado um mapeamento YAML")
    return Perfil.model_validate(dados)
