"""Copiloto de formulário (modo SEMI-AUTOMÁTICO) para candidaturas na Gupy.

O que este módulo FAZ:
- abre um Chromium VISÍVEL com perfil persistente (~/.local/share/bot-vagas/navegador, 0700) em que o
  Thiago faz login sozinho (`login-gupy`);
- em `candidatar`, navega até a vaga e fica assistindo (polling ~1 s): a cada passo do formulário que o
  Thiago abrir, preenche UMA vez cada campo VAZIO reconhecido por texto (label, aria-label, placeholder,
  name/id, texto próximo; PT e EN) com o valor de respostas.yaml, anexa o PDF do currículo no
  `input[type=file]` de currículo e destaca o que não soube/não deve preencher.

O que este módulo NUNCA faz:
- clicar em qualquer elemento, disparar submit/Enter, `form.submit()`;
- marcar caixas de consentimento/termos/LGPD/aceite;
- responder perguntas abertas (textarea / "por que...") nem testes;
- digitar em campo de senha, e-mail de login ou captcha; contornar captcha/2FA/bloqueio;
- qualquer evasão de detecção (sem stealth, sem mexer em navigator.webdriver, sem UA falso);
- ler/guardar/logar senha, token ou cookies (a sessão fica só no perfil do Chromium, no disco local);
- sobrescrever valor já preenchido (a Gupy pré-preenche o perfil) ou campo que o usuário editou.

Os valores pessoais entram na página e nunca no terminal, no banco ou nos testes: o resumo registra só
o NOME do campo e o que aconteceu com ele.
"""

from __future__ import annotations

import json
import os
import re
import signal
import socket
import stat
import sys
import threading
import time
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

from .pagina import Resolver, URLRecusada, validar_url
from .respostas import _buscar, valores_sensiveis

HOSTS_GUPY: tuple[str, ...] = ("gupy.io",)
URL_PORTAL_GUPY = "https://portal.gupy.io/"
PASTA_PERFIL_PADRAO = Path.home() / ".local" / "share" / "bot-vagas" / "navegador"
PASTA_LOGS_PADRAO = Path.home() / ".local" / "share" / "bot-vagas" / "logs"


def pasta_logs() -> Path:
    """Pasta do log de depuração; BOT_VAGAS_LOGS troca o local (os testes usam pasta temporária)."""
    env = os.environ.get("BOT_VAGAS_LOGS")
    return Path(env) if env else PASTA_LOGS_PADRAO
INTERVALO_PADRAO = 1.0  # segundos entre varreduras

MENSAGEM_PERFIL_EM_USO = ("Já existe um navegador usando o perfil do bot (feche a janela do login-gupy ou de outro "
                          "candidatar) e tente de novo.")

ATR_ID = "data-bot-vagas-id"
ATR_ESTADO = "data-bot-vagas-estado"  # "preenchido" | "pendente"
ATR_EDITADO = "data-bot-vagas-editado"  # "1" quando o usuário mexeu no campo
ATR_FEITO = "data-bot-vagas-feito"  # "1" quando o bot já tratou o campo (preencheu ou decidiu não preencher)


class ErroCopiloto(RuntimeError):
    pass


class PerfilEmUso(ErroCopiloto):
    """Outro Chromium (login-gupy ou outro candidatar) já está com o perfil do bot aberto."""

    def __init__(self, mensagem: str = MENSAGEM_PERFIL_EM_USO):
        super().__init__(mensagem)


class HostNaoPermitido(URLRecusada):
    pass


# --- host / perfil -------------------------------------------------------------------

@dataclass(frozen=True)
class Alvos:
    """Allowlist de hosts. Em produção só gupy.io; `permitir_loopback` existe SÓ para os testes
    (páginas fixture em 127.0.0.1) e nunca é ligado pela CLI."""

    hosts: tuple[str, ...] = HOSTS_GUPY
    permitir_loopback: bool = False

    def host_permitido(self, host: str) -> bool:
        h = (host or "").lower().rstrip(".")
        if self.permitir_loopback and h in ("127.0.0.1", "localhost"):
            return True
        return any(h == base or h.endswith("." + base) for base in self.hosts)


ALVOS_PRODUCAO = Alvos()


def validar_url_candidatura(url: str, alvos: Alvos = ALVOS_PRODUCAO, resolver: Resolver | None = None) -> str:
    """Allowlist de host (gupy.io e subdomínios) + validação anti-SSRF de pagina.validar_url."""
    url = (url or "").strip()
    try:
        host = (urlsplit(url).hostname or "").lower()
    except ValueError:
        raise URLRecusada(f"URL malformada: {url!r}") from None
    if not alvos.host_permitido(host):
        raise HostNaoPermitido(
            f"host '{host or '(vazio)'}' fora da lista permitida ({', '.join(alvos.hosts)} e subdomínios). "
            "O copiloto só atua em páginas da Gupy."
        )
    if alvos.permitir_loopback and host in ("127.0.0.1", "localhost"):
        if urlsplit(url).scheme not in ("http", "https"):
            raise URLRecusada("só http/https")
        return url  # só em testes
    return validar_url(url, resolver)


def preparar_pasta_perfil(pasta: Path | str = PASTA_PERFIL_PADRAO) -> Path:
    """Cria (se preciso) a pasta do perfil do navegador fora do projeto e garante modo 0700."""
    pasta = Path(pasta).expanduser()
    pasta.mkdir(parents=True, exist_ok=True)
    modo = stat.S_IMODE(pasta.stat().st_mode)
    if modo != 0o700:
        os.chmod(pasta, 0o700)
    return pasta


AVISO_PERFIL = (
    "AVISO: o perfil do navegador em {pasta} guarda a SESSÃO da sua conta (cookies). Ele fica só neste "
    "computador, com permissão 0700, e o bot nunca lê, copia ou registra senha, token ou cookies."
)


def _processo_vivo(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def perfil_em_uso(pasta: Path | str) -> bool:
    """True se o Chromium deixou um SingletonLock (symlink 'host-pid') apontando para um processo vivo
    NESTA máquina. Lock de outro host ou de processo morto (queda) é ignorado: o Chromium o substitui."""
    lock = Path(pasta) / "SingletonLock"
    try:
        alvo = os.readlink(lock)
    except OSError:
        return False
    host, sep, pid = alvo.rpartition("-")
    if not sep or not pid.isdigit():
        return False
    if host and host != socket.gethostname():
        return False
    return _processo_vivo(int(pid))


_PADRAO_ERRO_PERFIL_EM_USO = re.compile(
    r"ProcessSingleton|profile (directory )?is already in use|existing browser session|"
    r"sess[aã]o de navegador existente|SingletonLock|profile .*in use", re.I)


# --- log local de depuração (calibração do reconhecimento) ----------------------------------

class LogCampos:
    """Log JSONL LOCAL (pasta 0700, arquivo 0600) com UM registro por campo de formulário visto: tipo, textos
    usados na classificação, categoria atribuída e o que o bot fez. NUNCA grava valores digitados, valores de
    respostas.yaml, senhas, e-mails, cookies ou a descrição da vaga: qualquer trecho de texto igual a um valor
    de respostas.yaml é substituído por "[omitido]" e a query string das URLs é descartada."""

    def __init__(self, pasta: Path | str | None = None, respostas: dict[str, Any] | None = None,
                 prefixo: str = "candidatar", extras_sensiveis: list[str] | tuple[str, ...] = ()):
        self.pasta = Path(pasta if pasta is not None else pasta_logs()).expanduser()
        self.pasta.mkdir(parents=True, exist_ok=True, mode=0o700)
        if stat.S_IMODE(self.pasta.stat().st_mode) != 0o700:
            os.chmod(self.pasta, 0o700)
        quando = datetime.now().strftime("%Y%m%d-%H%M%S")
        self.caminho = self.pasta / f"{prefixo}-{quando}.jsonl"
        n = 1
        while self.caminho.exists():
            n += 1
            self.caminho = self.pasta / f"{prefixo}-{quando}-{n}.jsonl"
        fd = os.open(self.caminho, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        self._f = os.fdopen(fd, "w", encoding="utf-8")
        os.chmod(self.caminho, 0o600)
        # do maior para o menor, para "Maceió, AL" sumir antes de "Maceió"
        sensiveis = set(valores_sensiveis(respostas)) | {str(x).strip() for x in extras_sensiveis if x}
        self._sensiveis = sorted({s for s in sensiveis if len(s) >= 4}, key=len, reverse=True)
        self._vistos: set[tuple] = set()
        self.registros = 0

    def _redigir(self, texto: Any, maximo: int = 300) -> str:
        s = re.sub(r"\s+", " ", str(texto or "")).strip()
        for v in self._sensiveis:
            s = re.sub(re.escape(v), "[omitido]", s, flags=re.I)
        s = _RE_EMAIL.sub("[e-mail omitido]", s)
        s = _RE_NUMERO_LONGO.sub("[número omitido]", s)  # telefone, CPF, CEP, cartão...
        return s[:maximo]

    @staticmethod
    def _url_sem_query(url: str) -> str:
        try:
            u = urlsplit(url)
            return f"{u.scheme}://{u.netloc}{u.path}"
        except ValueError:
            return ""

    def _gravar(self, registro: dict[str, Any]) -> None:
        registro = {"quando": datetime.now(timezone.utc).isoformat(timespec="seconds"), **registro}
        self._f.write(json.dumps(registro, ensure_ascii=False) + "\n")
        self._f.flush()
        self.registros += 1

    def tela(self, url: str, passo: int, campos: int, login: bool = False) -> None:
        self._gravar({"evento": "tela", "url": self._url_sem_query(url), "passo": passo, "campos_visiveis": campos,
                      "tela_de_login": login})

    def campo(self, url: str, c: dict[str, Any], categoria: str, resultado: str, motivo: str) -> None:
        u = self._url_sem_query(url)
        marca = (u, c.get("id"), categoria, resultado, motivo)
        if marca in self._vistos:
            return
        self._vistos.add(marca)
        textos = {k: self._redigir(c.get(k), 120 if k == "proximo" else 300)
                  for k in ("label", "aria", "placeholder", "name", "elid", "autocomplete", "title", "proximo")}
        textos = {k: v for k, v in textos.items() if v}
        reg: dict[str, Any] = {
            "evento": "campo", "url": u, "tag": c.get("tag"), "tipo": c.get("tipo"), "textos": textos,
            "obrigatorio": bool(c.get("required")), "desabilitado": bool(c.get("disabled")),
            "ja_tinha_valor": bool(c.get("value") or c.get("checked") or c.get("arquivos") or c.get("grupo_marcado")),
            "editado_pelo_usuario": bool(c.get("editado")),
            "categoria": _nome_categoria(categoria), "resultado": resultado or "pulou", "motivo": motivo,
        }
        if c.get("opcoes"):
            reg["opcoes"] = [self._redigir(o.get("text")) for o in c["opcoes"]][:30]
        self._gravar(reg)

    def evento(self, nome: str, **extra: Any) -> None:
        self._gravar({"evento": nome, **{k: self._redigir(v) if isinstance(v, str) else v for k, v in extra.items()}})

    def fechar(self) -> None:
        try:
            self._f.close()
        except Exception:
            pass


_RE_EMAIL = re.compile(r"[^\s@<>()\[\]]+@[^\s@<>()\[\]]+\.[A-Za-z]{2,}")
_RE_NUMERO_LONGO = re.compile(r"\+?\d[\d .()/-]{6,}\d")


def _nome_categoria(categoria: str) -> str:
    if not categoria:
        return "não reconhecido"
    if categoria in ("login", "consentimento", "aberta"):
        return f"proibido: {categoria}"
    return categoria


# --- reconhecimento por texto -----------------------------------------------------------

def _norm(s: str) -> str:
    nfkd = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in nfkd if not unicodedata.combining(c)).lower()
    return re.sub(r"\s+", " ", s).strip()


# Ordem importa: o primeiro padrão que casar vence (os mais específicos vêm antes).
# Para ampliar o reconhecimento, acrescente sinônimos ao regex da chave correspondente.
PADROES_CAMPOS: tuple[tuple[str, str], ...] = (
    ("extras.aceita_viajar", r"viajar|viagens|\btravel"),
    ("extras.aceita_trabalhar_fins_de_semana", r"fins? de semana|finais de semana|weekend"),
    ("extras.parente_na_empresa", r"parente|familiar(es)? na empresa|\brelative|family member|grau de parentesco"),
    ("extras.ja_trabalhou_na_empresa", r"ja trabalhou|trabalhou (na|nesta|em nossa|aqui)|ex-?(funcionario|colaborador)|"
                                        r"worked (at|for|here)|previously (employed|worked)|former employee"),
    ("extras.como_conheceu_a_vaga", r"como (voce )?(conheceu|ficou sabendo|soube|descobriu|chegou)|onde (viu|encontrou)|"
                                     r"how did you (hear|find|learn)|where did you (see|find)|fonte da vaga|origem da candidatura"),
    ("extras.possui_cnh", r"\bcnh\b|habilitacao|carteira de motorista|driver'?s? licen[cs]e|driving licen[cs]e"),
    ("extras.data_nascimento", r"nascimento|date of birth|birth ?date|\bdob\b|\bbirthday"),
    ("extras.previsao_formatura", r"formatura|conclusao (do|de) curso|previsao de (termino|conclusao)|termino do curso|"
                                   r"graduation|expected to graduate|graduate date|semestre atual|current semester"),
    ("dados_demograficos.pcd", r"\bpcd\b|deficiencia|disabilit"),
    ("dados_demograficos.raca_cor", r"\braca\b|\bcor\b|\betnia|ethnic|\brace\b"),
    ("dados_demograficos.genero", r"\bgenero\b|\bgender\b|\bsexo\b"),
    ("localizacao.aceita_mudar_de_cidade", r"mudar de cidade|mudanca de cidade|disposto a (se )?mudar|relocat"),
    ("localizacao.aceita_remoto", r"\bremoto\b|\bremote\b|home ?office|trabalho a distancia"),
    ("tipo_contrato_aceito", r"tipo de contrat|modelo de contrat|regime de contrat|contract type|employment type|"
                             r"modalidade de contrat"),
    ("disponibilidade.carga_horaria", r"carga horaria|horas (semanais|por semana|diarias|por dia)|hours? (per|a) (week|day)|"
                                        r"workload|jornada"),
    ("disponibilidade.turno_preferido", r"\bturno\b|\bshift\b|periodo (do dia|preferido)|horario preferido"),
    ("disponibilidade.inicio", r"disponibilidade (de|para) (inicio|comec|start)|inicio imediato|data de inicio|start date|"
                               r"available to start|quando (pode|poderia|podera) (comecar|iniciar)|disponibilidade"),
    ("pretensao_salarial.brl_mensal_minimo", r"pretens|salari|salary|remunera|compensation|\bbolsa"),
    ("autorizacao_trabalho.brasil", r"autoriza(cao|do) (de |para )?trabalh|work authoriz|legally (authorized|allowed)|"
                                    r"permissao de trabalho"),
    ("idiomas.ingles", r"\bingles\b|\benglish\b"),
    ("localizacao.cidade", r"\bcidade\b|\bcity\b|\bmunicipio\b"),
    ("localizacao.estado", r"\bestado\b|\bstate\b|\buf\b|provincia"),
    ("localizacao.pais", r"\bpais\b|\bcountry\b|nacionalidade"),
)

# Nunca preencher: autenticação / captcha.
PADRAO_LOGIN = r"\bsenha\b|password|\blogin\b|\bentrar\b|sign ?in|captcha|codigo de verificacao|verification code|\botp\b|e-?mail|usuario\b|username"
# Nunca marcar: consentimento/termos/aceite.
PADRAO_CONSENTIMENTO = (r"\baceit|concord|\bli e\b|li os|termos|politica de privacidade|\blgpd\b|consent|agree|\bterms\b|"
                        r"privacy|autorizo|autorizacao para (uso|tratamento)|ciente|declaro")
# Pergunta aberta/discursiva ou teste: deixar para o Thiago.
PADRAO_ABERTA = (r"por ?que|porque|conte|descreva|fale (um pouco )?sobre|explique|justifique|comente|\bwhy\b|tell us|describe|"
                 r"explain|cover letter|carta de apresentacao|motivac|\bteste\b|\bprova\b|\bquiz\b|desafio|redacao|essay")
PADRAO_CURRICULO = r"curricul|\bcv\b|resume|résumé|anexe seu|attach your"


def classificar(texto: str, tag: str = "input", tipo: str = "text") -> str:
    """Devolve a categoria do campo: 'login', 'consentimento', 'aberta', 'curriculo', 'ignorar' ou uma
    chave de respostas.yaml; '' quando não reconheceu."""
    t = _norm(texto)
    tipo = (tipo or "text").lower()
    if tipo in ("password", "email") or re.search(PADRAO_LOGIN, t):
        return "login"
    if tipo in ("submit", "button", "reset", "image", "hidden"):
        return "ignorar"
    if tipo == "file":
        return "curriculo" if re.search(PADRAO_CURRICULO, t) else ""
    if tipo == "checkbox" and re.search(PADRAO_CONSENTIMENTO, t):
        return "consentimento"
    if tag == "textarea" or re.search(PADRAO_ABERTA, t):
        return "aberta"
    if re.search(PADRAO_CONSENTIMENTO, t) and tipo in ("checkbox", "radio"):
        return "consentimento"
    for chave, padrao in PADROES_CAMPOS:
        if re.search(padrao, t):
            return chave
    return ""


# --- valores -----------------------------------------------------------------------

SIM = ("sim", "yes", "possuo", "tenho", "aceito", "verdadeiro", "true", "s")
NAO = ("nao", "no", "nao possuo", "nao tenho", "nenhum", "nenhuma", "falso", "false", "n")
SINONIMOS_VALOR: dict[str, tuple[str, ...]] = {
    "masculino": ("masculino", "homem", "male", "man", "homem cis", "homem cisgenero"),
    "feminino": ("feminino", "mulher", "female", "woman", "mulher cis", "mulher cisgenero"),
    "brasil": ("brasil", "brazil", "br"),
    "imediata": ("imediata", "imediato", "imediatamente", "immediately", "immediate", "agora", "ja"),
    "nativo": ("nativo", "native", "fluente", "fluent"),
    "avancado": ("avancado", "advanced", "fluente", "fluent"),
    "intermediario": ("intermediario", "intermediate"),
    "basico": ("basico", "basic", "beginner", "iniciante"),
    "estagio": ("estagio", "estagiario", "internship", "intern"),
    "clt": ("clt", "efetivo", "full-time", "full time", "tempo integral"),
    "pj": ("pj", "pessoa juridica", "contractor", "freelance", "prestador"),
    "trainee": ("trainee",),
}


def _data_iso(valor: str) -> tuple[int, int | None, int | None] | None:
    m = re.fullmatch(r"(\d{4})-(\d{2})(?:-(\d{2}))?", str(valor).strip())
    if not m:
        return None
    return int(m.group(1)), int(m.group(2)), (int(m.group(3)) if m.group(3) else None)


def formatar_para_campo(chave: str, valor: Any, tipo: str = "text", dica: str = "") -> str | None:
    """Texto a digitar em um campo de texto. None = não dá para preencher (nulo/ambíguo)."""
    if valor is None or valor == "" or valor == []:
        return None
    tipo = (tipo or "text").lower()
    dica_n = _norm(dica)
    if isinstance(valor, bool):
        return None  # booleano em campo de texto: ambíguo; fica para select/radio
    if isinstance(valor, list):
        return ", ".join(str(v) for v in valor)
    if isinstance(valor, (int, float)):
        return str(int(valor)) if float(valor).is_integer() else str(valor)
    s = str(valor).strip()
    d = _data_iso(s)
    if d:
        ano, mes, dia = d
        if tipo == "date":
            return f"{ano:04d}-{mes:02d}-{dia or 1:02d}" if dia else None
        if tipo == "month":
            return f"{ano:04d}-{mes:02d}"
        if dia:
            return f"{dia:02d}/{mes:02d}/{ano:04d}"  # dd/mm/aaaa (padrão BR) para texto/máscara
        if "dd" in dica_n:
            return None  # o campo pede dia e só temos mês/ano: não inventar
        if re.search(r"\bano\b|\byear\b|aaaa", dica_n) and not re.search(r"\bmes\b|month|mm", dica_n):
            return f"{ano:04d}"
        return f"{mes:02d}/{ano:04d}"
    return s


def escolher_opcao(valor: Any, opcoes: list[str]) -> int | None:
    """Índice da opção (texto) que corresponde ao valor; None se nenhuma OU mais de uma casar."""
    if valor is None or valor == "" or valor == []:
        return None
    textos = [_norm(o) for o in opcoes]
    if isinstance(valor, bool):
        alvos = SIM if valor else NAO
        cands = []
        for i, t in enumerate(textos):
            inicio = re.match(r"^(sim|nao|yes|no)\b", t)
            if t in alvos or t.split(",")[0].strip() in alvos:
                cands.append(i)
            elif inicio and ((inicio.group(1) in ("sim", "yes")) == valor):
                cands.append(i)
        return cands[0] if len(cands) == 1 else None
    valores = valor if isinstance(valor, list) else [valor]
    for v in valores:
        vn = _norm(str(v))
        sinonimos = set(SINONIMOS_VALOR.get(vn, ())) | {vn}
        if vn.isdigit():
            cands = [i for i, t in enumerate(textos) if re.search(rf"\b{re.escape(vn)}\b", t)]
        else:
            cands = [i for i, t in enumerate(textos) if t in sinonimos]
            if not cands:
                cands = [i for i, t in enumerate(textos) if any(re.search(rf"\b{re.escape(s)}\b", t) for s in sinonimos)]
            if not cands and len(vn) >= 4:  # parda/pardo, negra/negro...
                raiz = vn[:-1]
                cands = [i for i, t in enumerate(textos) if re.search(rf"\b{re.escape(raiz)}[aoe]?s?\b", t)]
        if len(cands) == 1:
            return cands[0]
        if len(cands) > 1:
            return None
    return None


# --- JS injetado -------------------------------------------------------------------

_JS_INSTALAR = r"""
() => {
  if (window.__botVagasInstalado) return false;
  window.__botVagasInstalado = true;
  window.__botVagasPreenchendo = false;
  // marca campos editados pelo usuário (eventos que o bot não causou)
  const marcar = (e) => {
    if (window.__botVagasPreenchendo) return;
    const el = e.target;
    if (el && el.setAttribute && /^(INPUT|SELECT|TEXTAREA)$/.test(el.tagName)) {
      el.setAttribute('data-bot-vagas-editado', '1');
      if (el.type === 'radio' && el.name) {
        document.querySelectorAll('input[type=radio][name="' + CSS.escape(el.name) + '"]')
          .forEach(r => r.setAttribute('data-bot-vagas-editado', '1'));
      }
    }
  };
  document.addEventListener('input', marcar, true);
  document.addEventListener('change', marcar, true);
  document.addEventListener('keydown', marcar, true);
  const estilo = document.createElement('style');
  estilo.id = 'bot-vagas-estilo';
  estilo.textContent = `
    [data-bot-vagas-estado="preenchido"] { outline: 2px dashed #2a9d6f !important; outline-offset: 2px !important; }
    [data-bot-vagas-estado="pendente"] { outline: 2px dashed #e08a00 !important; outline-offset: 2px !important; }
    #bot-vagas-painel { position: fixed; left: 8px; bottom: 8px; z-index: 2147483647; pointer-events: none;
      max-width: 340px; max-height: 45vh; overflow: hidden; font: 12px/1.35 system-ui, sans-serif;
      background: rgba(255,255,255,.93); color: #222; border: 1px solid #bbb; border-radius: 6px; padding: 8px 10px;
      box-shadow: 0 2px 8px rgba(0,0,0,.15); }
    #bot-vagas-painel b { display: block; margin-bottom: 4px; }
    #bot-vagas-painel .ok { color: #1f7a52; } #bot-vagas-painel .pend { color: #b36b00; }
  `;
  (document.head || document.documentElement).appendChild(estilo);
  return true;
}
"""

_JS_COLETAR = r"""
(ATR_ID) => {
  const vis = (el) => {
    if (!el || el.type === 'hidden') return false;
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden') return false;
    const r = el.getBoundingClientRect();
    return (r.width > 0 && r.height > 0) || el.type === 'file' || el.type === 'radio' || el.type === 'checkbox';
  };
  const txt = (el) => (el ? (el.innerText || el.textContent || '') : '').replace(/\s+/g, ' ').trim();
  const labelDe = (el) => {
    const partes = [];
    if (el.id) document.querySelectorAll('label[for="' + CSS.escape(el.id) + '"]').forEach(l => partes.push(txt(l)));
    const lp = el.closest('label'); if (lp) partes.push(txt(lp));
    const lb = el.getAttribute('aria-labelledby');
    if (lb) lb.split(/\s+/).forEach(id => { const e = document.getElementById(id); if (e) partes.push(txt(e)); });
    const db = el.getAttribute('aria-describedby');
    if (db) db.split(/\s+/).forEach(id => { const e = document.getElementById(id); if (e) partes.push(txt(e)); });
    return partes.join(' ');
  };
  const proximo = (el) => {
    // texto do contêiner mais próximo que tenha algum texto (até 4 níveis), sem o texto das opções
    let p = el.parentElement, n = 0;
    while (p && n < 4) {
      const t = txt(p);
      if (t && t.length <= 400) return t.slice(0, 300);
      p = p.parentElement; n++;
    }
    const fs = el.closest('fieldset'); const lg = fs ? fs.querySelector('legend') : null;
    return lg ? txt(lg) : '';
  };
  let seq = window.__botVagasSeq || 0;
  const saida = [];
  document.querySelectorAll('input, select, textarea').forEach((el) => {
    if (el.closest('#bot-vagas-painel')) return;
    if (!vis(el)) return;
    if (!el.getAttribute(ATR_ID)) { seq += 1; el.setAttribute(ATR_ID, 'bv' + seq); }
    const tipo = (el.tagName === 'SELECT') ? 'select' : (el.tagName === 'TEXTAREA' ? 'textarea' : (el.type || 'text').toLowerCase());
    const item = {
      id: el.getAttribute(ATR_ID), tag: el.tagName.toLowerCase(), tipo,
      name: el.name || '', elid: el.id || '', placeholder: el.placeholder || '',
      aria: el.getAttribute('aria-label') || '', label: labelDe(el), proximo: proximo(el),
      autocomplete: el.getAttribute('autocomplete') || '', pattern: el.getAttribute('pattern') || '',
      mask: el.getAttribute('data-mask') || el.getAttribute('mask') || '', title: el.title || '',
      required: !!(el.required || el.getAttribute('aria-required') === 'true'),
      disabled: !!(el.disabled || el.readOnly),
      editado: el.getAttribute('data-bot-vagas-editado') === '1',
      feito: el.getAttribute('data-bot-vagas-feito') === '1',
      estado: el.getAttribute('data-bot-vagas-estado') || '',
      value: '', checked: false, opcoes: [], arquivos: 0,
    };
    if (tipo === 'select') {
      item.opcoes = Array.from(el.options).map(o => ({ value: o.value, text: txt(o), disabled: o.disabled }));
      const sel = el.selectedOptions[0];
      item.value = (sel && sel.value && txt(sel) && !/^(selecione|select|escolha|choose|--)/i.test(txt(sel))) ? sel.value : '';
    } else if (tipo === 'checkbox' || tipo === 'radio') {
      item.checked = !!el.checked;
      if (tipo === 'radio' && el.name) {
        item.grupo_marcado = !!document.querySelector('input[type=radio][name="' + CSS.escape(el.name) + '"]:checked');
      }
    } else if (tipo === 'file') {
      item.arquivos = el.files ? el.files.length : 0;
    } else {
      item.value = el.value || '';
    }
    saida.push(item);
  });
  window.__botVagasSeq = seq;
  return saida;
}
"""

_JS_MARCAR = r"""
([ATR_ID, id, estado, feito, titulo]) => {
  const el = document.querySelector('[' + ATR_ID + '="' + id + '"]');
  if (!el) return false;
  if (estado) el.setAttribute('data-bot-vagas-estado', estado);
  if (feito) el.setAttribute('data-bot-vagas-feito', '1');
  if (titulo) el.setAttribute('title', titulo);
  return true;
}
"""

_JS_MARCAR_RADIO = r"""
([ATR_ID, id]) => {
  // marca um radio/checkbox SEM clicar: define checked e dispara só input/change (nunca click)
  const el = document.querySelector('[' + ATR_ID + '="' + id + '"]');
  if (!el || el.disabled) return false;
  window.__botVagasPreenchendo = true;
  try {
    el.checked = true;
    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
  } finally { window.__botVagasPreenchendo = false; }
  return !!el.checked;
}
"""

_JS_PAINEL = r"""
([preenchidos, pendentes, titulo]) => {
  let p = document.getElementById('bot-vagas-painel');
  if (!p) { p = document.createElement('div'); p.id = 'bot-vagas-painel'; document.body.appendChild(p); }
  const esc = (s) => String(s).replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
  let html = '<b>' + esc(titulo) + '</b>';
  if (preenchidos.length) html += '<div class="ok">preencheu — confira: ' + preenchidos.map(esc).join(', ') + '</div>';
  if (pendentes.length) html += '<div class="pend">preencha você: ' + pendentes.map(esc).join(', ') + '</div>';
  if (!preenchidos.length && !pendentes.length) html += '<div>nenhum campo de formulário reconhecido nesta tela</div>';
  p.innerHTML = html;
  return true;
}
"""

_JS_TEM_LOGIN = r"""
() => !!document.querySelector('input[type=password]')
"""


# --- copiloto ------------------------------------------------------------------------

@dataclass
class Registro:
    """Histórico da assistência, SEM valores pessoais: só nome do campo e o que aconteceu."""

    preenchidos: list[dict[str, str]] = field(default_factory=list)
    pendentes: list[dict[str, str]] = field(default_factory=list)
    avisos: list[str] = field(default_factory=list)
    pdf_anexado: bool = False
    passos: int = 0
    encerramento: str = ""  # como a sessão terminou: janela fechada / interrompida / erro
    log_campos: str = ""  # caminho do log local de depuração (se ligado)
    _vistos: set[tuple[str, str, str, str]] = field(default_factory=set, repr=False)

    def _add(self, lista: list[dict[str, str]], campo: str, **extra: str) -> None:
        """Acrescenta uma entrada (campo + metadados, nunca o valor), sem repetir. `extra` inclui a url."""
        marca = (extra.get("url", ""), campo, extra.get("motivo", ""), extra.get("como", ""))
        if marca in self._vistos:
            return
        self._vistos.add(marca)
        lista.append({"campo": campo, **extra})

    def para_json(self) -> dict[str, Any]:
        return {
            "quando": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "preenchidos": self.preenchidos, "pendentes": self.pendentes, "avisos": self.avisos,
            "pdf_anexado": self.pdf_anexado, "passos": self.passos, "encerramento": self.encerramento,
            "log_campos": self.log_campos,
        }


def _descricao_campo(c: dict[str, Any]) -> str:
    for k in ("label", "aria", "placeholder", "proximo", "name", "elid"):
        v = re.sub(r"\s*\*+\s*$", "", (c.get(k) or "").strip())  # sem o "*" de obrigatório
        if v:
            return re.sub(r"\s+", " ", v)[:60]
    return f"<{c.get('tag')} {c.get('tipo')}>"


def classificar_campo(c: dict[str, Any]) -> str:
    """Classifica pelo texto PRÓPRIO do campo primeiro (rótulo, aria, placeholder, título; depois name/id/
    autocomplete) e só usa o texto em volta (`proximo`) se nada casar. Assim um rótulo de outro campo ou uma
    palavra solta na página ("e-mail" num aviso de newsletter) não decide a categoria deste campo."""
    tag, tipo = c.get("tag") or "input", c.get("tipo") or "text"
    camadas = (("label", "aria", "placeholder", "title"), ("name", "elid", "autocomplete"), ("proximo",))
    tem_texto = False
    for chaves in camadas:
        texto = " | ".join(str(c.get(k) or "") for k in chaves).strip(" |")
        if not texto:
            continue
        tem_texto = True
        cat = classificar(texto, tag, tipo)
        if cat:
            return cat
    return "" if tem_texto else classificar("", tag, tipo)


def _texto_campo(c: dict[str, Any]) -> str:
    return " | ".join(str(c.get(k) or "") for k in ("label", "aria", "placeholder", "name", "elid", "autocomplete", "title", "proximo"))


class Copiloto:
    TITULO_PAINEL = "bot-vagas (copiloto) — nunca clica nem envia"

    def __init__(self, respostas: dict[str, Any] | None, pdf: Path | str | None, *, alvos: Alvos = ALVOS_PRODUCAO,
                 log: Callable[..., None] = print, log_campos: LogCampos | None = None,
                 notificar: Callable[[str], Any] | None = None):
        self.respostas = respostas or {}
        self.notificar = notificar
        self.pdf = Path(pdf) if pdf else None
        self.alvos = alvos
        self.log = log
        self.log_campos = log_campos
        self.registro = Registro()
        if log_campos is not None:
            self.registro.log_campos = str(log_campos.caminho)
        # por ABA: tela (host+path) em que saiu o último aviso de login. Com várias abas abertas, cada uma tem o
        # seu estado; assim o polling de uma aba não "reseta" o aviso da outra (o aviso sai uma vez por mudança).
        self._login_por_aba: dict[int, str] = {}
        self._telas_vistas: set[str] = set()
        self._grupos_feitos: set[tuple[str, str]] = set()

    @staticmethod
    def _tela(url: str) -> str:
        """Identifica a tela sem query/fragmento: SPAs e redirecionamentos de login mudam parâmetros a cada
        carregamento, e o aviso deve sair UMA vez por tela, não por polling."""
        try:
            u = urlsplit(url)
            return f"{u.scheme}://{u.netloc}{u.path}"
        except ValueError:
            return url

    # -- utilidades -----------------------------------------------------------------

    def url_permitida(self, url: str) -> bool:
        try:
            return self.alvos.host_permitido(urlsplit(url).hostname or "")
        except ValueError:
            return False

    def instalar(self, pagina) -> None:
        """Injeta (uma vez por documento) o listener de edição do usuário e o CSS. Idempotente."""
        pagina.evaluate(_JS_INSTALAR)

    def _loc(self, pagina, cid: str):
        return pagina.locator(f"[{ATR_ID}='{cid}']").first

    def _marcar(self, pagina, cid: str, estado: str, titulo: str, feito: bool = True) -> None:
        pagina.evaluate(_JS_MARCAR, [ATR_ID, cid, estado, feito, titulo])

    # -- um passo de assistência -----------------------------------------------------

    def passo(self, pagina) -> dict[str, list[str]]:
        """Varre a página UMA vez: preenche o que dá, marca o resto. Devolve nomes (não valores)."""
        url = pagina.url
        feitos: list[str] = []
        pendentes: list[str] = []
        if not self.url_permitida(url):
            return {"preenchidos": feitos, "pendentes": pendentes}  # aba fora do allowlist: não tocar
        self.registro.passos += 1
        self.instalar(pagina)
        tela = self._tela(url)
        tela_nova = tela not in self._telas_vistas
        self._telas_vistas.add(tela)
        aba = id(pagina)
        if pagina.evaluate(_JS_TEM_LOGIN):
            if self._login_por_aba.get(aba) != tela:
                self._login_por_aba[aba] = tela
                self.log("PRECISA DE LOGIN: a página tem campo de senha. Faça login você mesmo; o bot não toca "
                         "em campos de login, e-mail ou captcha e segue aguardando.")
                self.registro.avisos.append("tela de login detectada; aguardando o usuário")
                if self.log_campos is not None:
                    self.log_campos.tela(url, self.registro.passos, 0, login=True)
                if self.notificar is not None:
                    try:
                        self.notificar("login")  # texto genérico e fixo; nada da vaga
                    except Exception:
                        pass
            return {"preenchidos": feitos, "pendentes": pendentes}
        if aba in self._login_por_aba and self._login_por_aba[aba] != tela:
            del self._login_por_aba[aba]  # esta aba saiu da tela de login; se voltar a ela, avisa de novo

        campos: list[dict[str, Any]] = pagina.evaluate(_JS_COLETAR, ATR_ID)
        if self.log_campos is not None and tela_nova:
            self.log_campos.tela(url, self.registro.passos, len(campos))
        for c in campos:
            try:
                resultado = self._tratar(pagina, c, url)
                if resultado == "preenchido" and not c.get("_motivo"):
                    c["_motivo"] = "preenchido com respostas.yaml"
            except Exception as e:  # elemento sumiu/re-renderizou: tenta no próximo passo
                self.registro.avisos.append(f"falha ao tratar campo '{_descricao_campo(c)}': {type(e).__name__}")
                if self.log_campos is not None:
                    self.log_campos.campo(url, c, c.get("_categoria", ""), "erro", f"falha: {type(e).__name__}")
                continue
            if self.log_campos is not None and not c["feito"]:
                self.log_campos.campo(url, c, c.get("_categoria", ""), resultado, c.get("_motivo", ""))
            if resultado == "preenchido":
                feitos.append(_descricao_campo(c))
            elif resultado == "pendente":
                pendentes.append(_descricao_campo(c))
        # painel-resumo na página (pointer-events: none; só leitura)
        todos_feitos = sorted({p["campo"] for p in self.registro.preenchidos if p.get("url") == url})
        todos_pend = sorted({p["campo"] for p in self.registro.pendentes if p.get("url") == url})
        if todos_feitos or todos_pend or campos:
            pagina.evaluate(_JS_PAINEL, [todos_feitos, todos_pend, self.TITULO_PAINEL])
        return {"preenchidos": feitos, "pendentes": pendentes}

    def _tratar(self, pagina, c: dict[str, Any], url: str) -> str:
        """Devolve 'preenchido', 'pendente' ou '' (nada a fazer / já tratado)."""
        if c["feito"]:
            return ""
        nome = _descricao_campo(c)
        categoria = classificar_campo(c)
        c["_categoria"] = categoria
        cid = c["id"]

        if c["disabled"]:
            return self._pular(c, "campo desabilitado/somente leitura")
        if categoria == "login":
            return self._pular(c, "campo de login/e-mail/captcha: nunca tocado")
        if categoria == "ignorar":
            return self._pular(c, "botão/oculto: não é campo de resposta")
        if c["editado"]:
            self._marcar(pagina, cid, "", "", feito=True)
            return self._pular(c, "editado pelo usuário: nunca tocar")
        if categoria == "consentimento":
            if c["required"] and not c["checked"]:
                self._pendente(pagina, c, url, "consentimento/aceite: só você marca")
                return "pendente"
            self._marcar(pagina, cid, "", "", feito=True)
            return self._pular(c, "consentimento já marcado ou opcional: não tocar")
        if categoria == "aberta":
            if c["value"]:
                self._marcar(pagina, cid, "", "", feito=True)
                return self._pular(c, "pergunta aberta já respondida")
            if c["required"] or c["tag"] == "textarea":
                self._pendente(pagina, c, url, "pergunta aberta/discursiva: responda você")
                return "pendente"
            return self._pular(c, "pergunta aberta opcional: deixada para você")
        if c["tipo"] == "file":
            if categoria != "curriculo":
                if c["required"] and not c["arquivos"]:
                    self._pendente(pagina, c, url, "anexo que não é currículo")
                    return "pendente"
                return self._pular(c, "anexo opcional que não é currículo")
            if c["arquivos"]:
                self._marcar(pagina, cid, "", "", feito=True)
                return self._pular(c, "currículo já anexado")
            if not self.pdf or not self.pdf.exists():
                self._pendente(pagina, c, url, "PDF do currículo não encontrado")
                return "pendente"
            self._loc(pagina, cid).set_input_files(str(self.pdf))  # set_input_files não clica
            self._marcar(pagina, cid, "preenchido", "bot-vagas anexou o currículo — confira")
            self.registro.pdf_anexado = True
            c["_motivo"] = "PDF do currículo anexado"
            self.registro._add(self.registro.preenchidos, nome, chave="curriculo", como="anexo", url=url)
            return "preenchido"
        if not categoria:
            if c["required"] and not c["value"] and not c["checked"] and not c.get("grupo_marcado"):
                self._pendente(pagina, c, url, "campo não reconhecido")
                return "pendente"
            return self._pular(c, "não reconhecido e opcional (ou já com valor)")

        # chave de respostas.yaml
        valor = _buscar(self.respostas, categoria)
        if valor is None or valor == "" or valor == []:
            if c["tipo"] == "radio" and c.get("grupo_marcado"):
                return self._pular(c, "grupo de opções já marcado")
            if c["value"] or c["checked"]:
                self._marcar(pagina, cid, "", "", feito=True)
                return self._pular(c, "já tinha valor (pré-preenchido): não sobrescrever")
            self._pendente(pagina, c, url, "sem resposta em respostas.yaml (nulo)")
            return "pendente"

        tipo = c["tipo"]
        if tipo == "select":
            if c["value"]:
                self._marcar(pagina, cid, "", "", feito=True)
                return self._pular(c, "já tinha valor (pré-preenchido): não sobrescrever")
            opcoes = [o for o in c["opcoes"] if not o["disabled"] and o["value"] != ""]
            idx = escolher_opcao(valor, [o["text"] for o in opcoes])
            if idx is None:
                self._pendente(pagina, c, url, "nenhuma opção corresponde sem ambiguidade")
                return "pendente"
            self._com_flag(pagina, lambda: self._loc(pagina, cid).select_option(value=opcoes[idx]["value"]))
            self._marcar(pagina, cid, "preenchido", "bot-vagas preencheu — confira")
            self.registro._add(self.registro.preenchidos, nome, chave=categoria, como="select", url=url)
            return "preenchido"
        if tipo == "radio":
            grupo = (url, c["name"] or c["proximo"])
            if c.get("grupo_marcado") or grupo in self._grupos_feitos:
                self._marcar(pagina, cid, "", "", feito=True)
                return self._pular(c, "grupo de opções já marcado/tratado")
            # avalia o GRUPO inteiro de uma vez
            irmaos = pagina.evaluate(
                "([ATR, nome]) => Array.from(document.querySelectorAll('input[type=radio]')).filter(r => r.name === nome)"
                ".map(r => ({id: r.getAttribute(ATR), texto: (r.labels && r.labels[0] ? r.labels[0].innerText : '')"
                " || (r.closest('label') ? r.closest('label').innerText : '') || r.value}))", [ATR_ID, c["name"]]) if c["name"] else [c | {"texto": c["label"] or c["proximo"]}]
            idx = escolher_opcao(valor, [i["texto"] for i in irmaos])
            self._grupos_feitos.add(grupo)
            if idx is None:
                self._pendente(pagina, c, url, "nenhuma opção corresponde sem ambiguidade")
                return "pendente"
            ok = self._marcar_opcao(pagina, irmaos[idx]["id"])
            for i in irmaos:
                self._marcar(pagina, i["id"], "preenchido" if i["id"] == irmaos[idx]["id"] else "", "bot-vagas marcou — confira")
            if not ok:
                self._pendente(pagina, c, url, "não consegui marcar a opção")
                return "pendente"
            self.registro._add(self.registro.preenchidos, nome, chave=categoria, como="radio (sem clique)", url=url)
            return "preenchido"
        if tipo == "checkbox":
            if not isinstance(valor, bool):
                self._pendente(pagina, c, url, "caixa de seleção com resposta não sim/não")
                return "pendente"
            if c["checked"] or not valor:
                self._marcar(pagina, cid, "", "", feito=True)
                return self._pular(c, "já marcado, ou a resposta é não: deixar desmarcado")
            self._marcar_opcao(pagina, cid)
            self._marcar(pagina, cid, "preenchido", "bot-vagas marcou — confira")
            self.registro._add(self.registro.preenchidos, nome, chave=categoria, como="checkbox (sem clique)", url=url)
            return "preenchido"
        # texto / number / date / month / tel...
        if c["value"]:
            self._marcar(pagina, cid, "", "", feito=True)
            return self._pular(c, "já tinha valor (pré-preenchido): não sobrescrever")
        dica = " ".join(str(c.get(k) or "") for k in ("placeholder", "pattern", "mask", "label", "aria", "title"))
        texto = formatar_para_campo(categoria, valor, tipo, dica)
        if texto is None:
            self._pendente(pagina, c, url, "resposta não cabe neste tipo de campo")
            return "pendente"
        if tipo == "number" and not re.fullmatch(r"-?\d+(\.\d+)?", texto):
            self._pendente(pagina, c, url, "campo numérico e a resposta não é número")
            return "pendente"
        self._com_flag(pagina, lambda: self._loc(pagina, cid).fill(texto))  # fill: foca e digita; não clica
        self._marcar(pagina, cid, "preenchido", "bot-vagas preencheu — confira")
        self.registro._add(self.registro.preenchidos, nome, chave=categoria, como="texto", url=url)
        return "preenchido"

    def _marcar_opcao(self, pagina, cid: str) -> bool:
        """Copiloto: marca radio/checkbox SEM clicar (define checked e dispara input/change)."""
        return bool(pagina.evaluate(_JS_MARCAR_RADIO, [ATR_ID, cid]))

    def _com_flag(self, pagina, acao: Callable[[], Any]) -> None:
        pagina.evaluate("() => { window.__botVagasPreenchendo = true; }")
        try:
            acao()
        finally:
            pagina.evaluate("() => { window.__botVagasPreenchendo = false; }")

    def _pendente(self, pagina, c: dict[str, Any], url: str, motivo: str) -> None:
        c["_motivo"] = motivo
        self._marcar(pagina, c["id"], "pendente", f"bot-vagas: preencha você ({motivo})")
        self.registro._add(self.registro.pendentes, _descricao_campo(c), motivo=motivo, url=url)

    @staticmethod
    def _pular(c: dict[str, Any], motivo: str) -> str:
        """Campo que o bot decidiu NÃO tocar (e não precisa destacar). Só registra o motivo (para o log)."""
        c["_motivo"] = motivo
        return ""

    # -- resumo ----------------------------------------------------------------------

    def resumo(self) -> str:
        r = self.registro
        linhas = ["", "RESUMO DA ASSISTÊNCIA (sem valores pessoais; o envio foi/é decisão sua):",
                  f"  passos de varredura: {r.passos}; PDF anexado: {'sim' if r.pdf_anexado else 'não'}",
                  f"  campos preenchidos pelo bot ({len(r.preenchidos)}):"]
        linhas += [f"    - {p['campo']}  [{p.get('chave', '')}, {p.get('como', '')}]" for p in r.preenchidos] or ["    (nenhum)"]
        linhas.append(f"  campos NÃO preenchidos ({len(r.pendentes)}):")
        linhas += [f"    - {p['campo']}: {p.get('motivo', '')}" for p in r.pendentes] or ["    (nenhum)"]
        if r.avisos:
            linhas.append("  avisos:")
            linhas += [f"    - {a}" for a in r.avisos]
        if r.encerramento:
            linhas.append(f"  encerramento: {r.encerramento}")
        if r.log_campos:
            linhas.append(f"  log de depuração (local, sem valores): {r.log_campos}")
        linhas.append("  Quando realmente enviar: bot-vagas marcar <chave|url> enviada")
        return "\n".join(linhas)


# --- navegador visível (só na CLI) -------------------------------------------------------

def abrir_contexto_persistente(p, pasta_perfil: Path, *, headless: bool = False, canal: str | None = None):
    """Chromium com perfil persistente. Sem stealth, sem UA falso, sem mexer em navigator.webdriver.
    Se o perfil já estiver aberto por outro Chromium, levanta PerfilEmUso (mensagem curta, sem traceback).
    `canal` existe só para os testes (o Chromium completo em headless cria o SingletonLock como o visível)."""
    from playwright.sync_api import Error as ErroPW

    if perfil_em_uso(pasta_perfil):
        raise PerfilEmUso()
    opcoes: dict[str, Any] = {}
    if canal:
        opcoes["channel"] = canal
    try:
        return p.chromium.launch_persistent_context(
            user_data_dir=str(pasta_perfil), headless=headless, locale="pt-BR",
            viewport=None if not headless else {"width": 1280, "height": 900}, accept_downloads=False, **opcoes,
        )
    except ErroPW as e:
        msg = str(e)
        if _PADRAO_ERRO_PERFIL_EM_USO.search(msg) or (
                "has been closed" in msg and (Path(pasta_perfil) / "SingletonLock").is_symlink()):
            raise PerfilEmUso() from None
        raise


class _Encerramento:
    """Guarda o PRIMEIRO motivo de fim da sessão (janela fechada, Ctrl+C, SIGTERM, PARAR...)."""

    def __init__(self):
        self.motivo = ""

    def pedir(self, motivo: str) -> None:
        if not self.motivo:
            self.motivo = motivo

    def __bool__(self) -> bool:
        return bool(self.motivo)


MOTIVO_JANELA = "janela fechada"
MOTIVO_CTRL_C = "interrompido (Ctrl+C)"
MOTIVO_SIGTERM = "interrompido (SIGTERM)"
MOTIVO_PARAR = "parada pedida (arquivo PARAR)"
MOTIVO_NAVEGADOR = "navegador encerrado"
MOTIVOS_INTERRUPCAO = (MOTIVO_CTRL_C, MOTIVO_SIGTERM)


def _instalar_sinais(fim: _Encerramento) -> Callable[[], None]:
    """Ctrl+C / SIGTERM viram um PEDIDO de encerramento (sem KeyboardInterrupt no meio do Playwright): o laço
    termina no próximo ciclo e o resumo é impresso e gravado. Um SEGUNDO Ctrl+C volta ao comportamento padrão
    (KeyboardInterrupt), também tratado. Só funciona na thread principal (nos testes, em thread, não instala)."""
    if threading.current_thread() is not threading.main_thread():
        return lambda: None
    anteriores: dict[int, Any] = {}

    def tratador(signum, _frame):
        fim.pedir(MOTIVO_CTRL_C if signum == signal.SIGINT else MOTIVO_SIGTERM)
        try:
            signal.signal(signal.SIGINT, signal.default_int_handler)
        except (ValueError, OSError):
            pass

    for s in (signal.SIGINT, signal.SIGTERM):
        try:
            anteriores[s] = signal.getsignal(s)
            signal.signal(s, tratador)
        except (ValueError, OSError):
            pass

    def restaurar() -> None:
        for s, h in anteriores.items():
            try:
                signal.signal(s, h if h is not None else signal.SIG_DFL)
            except (ValueError, OSError, TypeError):
                pass

    return restaurar


def _abertas(ctx) -> list:
    try:
        return [pg for pg in ctx.pages if not pg.is_closed()]
    except Exception:
        return []


def _esperar(ctx, segundos: float, fim: _Encerramento, falhas: list[int]) -> None:
    """Espera `segundos` DENTRO do Playwright (page.wait_for_timeout), para que os eventos de fechamento de
    aba/janela sejam processados. `time.sleep` puro não processa eventos no modo síncrono: era por isso que
    fechar a janela não encerrava o `candidatar`."""
    from playwright.sync_api import Error as ErroPW

    abertas = _abertas(ctx)
    if not abertas:
        fim.pedir(MOTIVO_JANELA)
        return
    try:
        abertas[0].wait_for_timeout(max(1, int(segundos * 1000)))
        falhas[0] = 0
    except ErroPW:
        falhas[0] += 1
        if not _abertas(ctx):
            fim.pedir(MOTIVO_JANELA)
        elif falhas[0] >= 3:
            fim.pedir(MOTIVO_NAVEGADOR)  # conexão com o navegador caiu
        else:
            time.sleep(min(segundos, 0.5))


def _sessao(url: str, pasta: Path, fim: _Encerramento, *, headless: bool, canal: str | None, intervalo: float,
            ciclo: Callable[[Any], None], max_ciclos: int | None, max_segundos: float | None,
            parar: Callable[[], bool] | None) -> None:
    """Abre o Chromium, vai até `url` e repete `ciclo(ctx)` até: janela fechada, Ctrl+C/SIGTERM, PARAR,
    navegador encerrado ou limite (testes). Não deixa escapar erro do Playwright por fechamento."""
    from playwright.sync_api import Error as ErroPW, sync_playwright

    inicio = time.monotonic()
    pw = sync_playwright().start()
    try:
        ctx = abrir_contexto_persistente(pw, pasta, headless=headless, canal=canal)
        try:
            ctx.on("close", lambda *_: fim.pedir(MOTIVO_JANELA))
            pagina = ctx.pages[0] if ctx.pages else ctx.new_page()
            if not fim:
                try:
                    pagina.goto(url, wait_until="domcontentloaded")
                except ErroPW:
                    if not _abertas(ctx):
                        fim.pedir(MOTIVO_JANELA)
            ciclos, falhas = 0, [0]
            try:
                while not fim:
                    if parar is not None and parar():
                        fim.pedir(MOTIVO_PARAR)
                        break
                    if not _abertas(ctx):
                        fim.pedir(MOTIVO_JANELA)
                        break
                    ciclo(ctx)
                    ciclos += 1
                    if max_ciclos is not None and ciclos >= max_ciclos:
                        fim.pedir("limite de ciclos")
                        break
                    if max_segundos is not None and time.monotonic() - inicio > max_segundos:
                        fim.pedir("limite de tempo")
                        break
                    if not fim:
                        _esperar(ctx, intervalo, fim, falhas)
            except KeyboardInterrupt:  # registra ANTES do ctx.close() abaixo (que dispara o evento "close")
                fim.pedir(MOTIVO_CTRL_C)
                raise
            except ErroPW:
                fim.pedir(MOTIVO_JANELA if not _abertas(ctx) else MOTIVO_NAVEGADOR)
                raise
            except BaseException as e:
                fim.pedir(f"erro: {type(e).__name__}")
                raise
        finally:
            try:
                ctx.close()
            except Exception:
                pass
    except KeyboardInterrupt:  # segundo Ctrl+C (ou sinal antes de instalar o tratador)
        fim.pedir(MOTIVO_CTRL_C)
    except PerfilEmUso:
        raise
    except ErroPW:
        # fechamento/queda do navegador no meio de uma chamada: fim normal da sessão
        fim.pedir(MOTIVO_NAVEGADOR)
    finally:
        try:
            pw.stop()
        except Exception:
            pass


def login_gupy(pasta_perfil: Path | str = PASTA_PERFIL_PADRAO, *, url: str = URL_PORTAL_GUPY, log=print,
               headless: bool = False, max_segundos: float | None = None, canal: str | None = None,
               parar: Callable[[], bool] | None = None) -> int:
    """Abre o portal da Gupy para o Thiago fazer login. Não lê nem registra nada da sessão.
    Devolve 0 ao fechar a janela; 130 se interrompido; levanta PerfilEmUso se o perfil já estiver aberto."""
    pasta = preparar_pasta_perfil(pasta_perfil)
    if perfil_em_uso(pasta):
        raise PerfilEmUso()
    log(AVISO_PERFIL.format(pasta=pasta))
    log(f"abrindo {url} em um Chromium visível. Faça login você mesmo; feche a janela quando terminar.")
    fim = _Encerramento()
    restaurar = _instalar_sinais(fim)
    try:
        _sessao(url, pasta, fim, headless=headless, canal=canal, intervalo=0.5, ciclo=lambda ctx: None,
                max_ciclos=None, max_segundos=max_segundos, parar=parar)
    finally:
        restaurar()
    if fim.motivo == MOTIVO_PARAR:
        log("PARADO pelo botão PARAR: janela fechada pelo bot.")
        return 4
    if fim.motivo in MOTIVOS_INTERRUPCAO:
        log(f"{fim.motivo}: janela fechada. A sessão (se você logou) ficou no perfil local.")
        return 130
    log("janela fechada. A sessão (se você logou) ficou no perfil local; nada foi lido pelo bot.")
    return 0


def assistir(url: str, copiloto: Copiloto, *, pasta_perfil: Path | str = PASTA_PERFIL_PADRAO, headless: bool = False,
             intervalo: float = INTERVALO_PADRAO, max_passos: int | None = None, log=print, canal: str | None = None,
             parar: Callable[[], bool] | None = None) -> Registro:
    """Abre a vaga no Chromium visível e repete `copiloto.passo` em todas as abas permitidas até a janela fechar,
    Ctrl+C/SIGTERM ou PARAR. SEMPRE devolve o registro com `encerramento` preenchido (exceto PerfilEmUso, que
    acontece antes de abrir qualquer coisa). Nunca marca a vaga como enviada: isso é do `marcar`."""
    from playwright.sync_api import Error as ErroPW

    pasta = preparar_pasta_perfil(pasta_perfil)
    if perfil_em_uso(pasta):
        raise PerfilEmUso()
    log(AVISO_PERFIL.format(pasta=pasta))
    log("o bot NÃO clica em nada: você clica em 'Candidatar-se', 'Próximo' e 'Enviar'. "
        "Ele só preenche campos vazios reconhecidos, anexa o PDF e destaca o resto. Feche a janela para terminar "
        "(Ctrl+C também encerra e grava o resumo).")

    def ciclo(ctx) -> None:
        for pg in _abertas(ctx):
            try:
                if not copiloto.url_permitida(pg.url):
                    continue
                r = copiloto.passo(pg)
                for n in r["preenchidos"]:
                    log(f"  preencheu: {n}  (confira)")
                for n in r["pendentes"]:
                    log(f"  preencha você: {n}")
            except ErroPW:
                continue  # navegação/fechamento no meio da varredura: tenta no próximo ciclo

    fim = _Encerramento()
    restaurar = _instalar_sinais(fim)
    try:
        _sessao(url, pasta, fim, headless=headless, canal=canal, intervalo=intervalo, ciclo=ciclo,
                max_ciclos=max_passos, max_segundos=None, parar=parar)
    except PerfilEmUso:
        raise
    except BaseException as e:
        copiloto.registro.encerramento = f"erro: {type(e).__name__}"
        raise
    finally:
        restaurar()
        if not copiloto.registro.encerramento:
            copiloto.registro.encerramento = fim.motivo or "fim"
        if copiloto.log_campos is not None:
            try:
                copiloto.log_campos.evento("fim", encerramento=copiloto.registro.encerramento,
                                           passos=copiloto.registro.passos)
            except Exception:
                pass
    return copiloto.registro


def host_da_url(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


def json_registro(registro: Registro) -> str:
    return json.dumps(registro.para_json(), ensure_ascii=False)
