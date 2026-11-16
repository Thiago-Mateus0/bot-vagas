"""Painel web local (Etapa 3): SOMENTE LEITURA.

- FastAPI + Jinja2, HTML servido pelo próprio processo, sem JavaScript, sem CDN, sem fontes externas.
- Escuta apenas em 127.0.0.1 (ver `servir`). Sem autenticação, por ser local, mas:
  * rejeita requisições cujo header Host não seja localhost/127.0.0.1/[::1] (DNS rebinding);
  * todo texto vindo de vagas (título, empresa, descrição, motivo...) é dado NÃO confiável e passa
    pelo autoescape do Jinja (nunca `|safe`);
  * links externos só são renderizados como <a> quando o esquema é http/https, sempre com
    rel="noopener noreferrer".
- Nenhuma rota escreve no banco, chama a API da Anthropic ou faz rede.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from jinja2 import Environment, FileSystemLoader, select_autoescape

from .banco import BANCO_PADRAO, STATUS, Banco
from .gasto import CUSTOS_PADRAO, Contador, carregar_custos

PASTA = Path(__file__).parent
TEMPLATES = PASTA / "templates" / "painel"
STATIC = PASTA / "static"

HOSTS_PERMITIDOS = frozenset({"localhost", "127.0.0.1", "[::1]", "::1"})
ORDENACOES = ("nota", "data")
LIMITE_AVISO = 0.8  # fração do teto a partir da qual o painel avisa

ROTULOS_STATUS = {
    "nova": "Nova",
    "bloqueada": "Bloqueada",
    "avaliada": "Avaliada",
    "abaixo_do_corte": "Abaixo do corte",
    "aprovada": "Aprovada",
    "preparada": "Preparada",
    "em_candidatura": "Em candidatura",
    "enviada": "Enviada",
    "desistiu": "Desistiu",
}


# --- helpers puros (testáveis sem servidor) --------------------------------------

def url_segura(url: str | None) -> str | None:
    """Devolve a URL só se for http(s) absoluta; qualquer outra coisa (javascript:, data:, ftp:...)
    vira None e o template mostra o texto sem link."""
    if not url:
        return None
    try:
        p = urlsplit(url.strip())
    except ValueError:
        return None
    if p.scheme.lower() not in ("http", "https") or not p.netloc:
        return None
    return url.strip()


def host_permitido(host: str | None) -> bool:
    if not host:
        return False
    h = host.strip().lower()
    if h.startswith("["):  # IPv6 com porta: [::1]:8765
        h = h.split("]")[0] + "]"
    else:
        h = h.split(":")[0]
    return h in HOSTS_PERMITIDOS


def _detalhes(row) -> dict[str, Any]:
    try:
        d = json.loads(row["detalhes"]) if row["detalhes"] else {}
    except (ValueError, TypeError):
        return {}
    return d if isinstance(d, dict) else {}


def _lista_str(valor) -> list[str]:
    if not isinstance(valor, list):
        return []
    return [str(x) for x in valor]


def vaga_para_tela(row) -> dict[str, Any]:
    d = _detalhes(row)
    av = d.get("avaliacao") if isinstance(d.get("avaliacao"), dict) else {}
    prep = d.get("preparacao") if isinstance(d.get("preparacao"), dict) else {}
    assist = d.get("assistencia") if isinstance(d.get("assistencia"), dict) else {}
    return {
        # copiloto (candidatar): só nomes de campos e motivos; nunca valores pessoais
        "assistencia": {
            "quando": str(assist.get("quando") or ""),
            "passos": int(assist.get("passos") or 0),
            "pdf_anexado": bool(assist.get("pdf_anexado")),
            "preenchidos": [str(x.get("campo", "")) for x in assist.get("preenchidos", []) if isinstance(x, dict)],
            "pendentes": [f"{x.get('campo', '')}: {x.get('motivo', '')}" for x in assist.get("pendentes", []) if isinstance(x, dict)],
            "avisos": _lista_str(assist.get("avisos")),
        } if assist else None,
        "marcacao": d.get("marcacao") if isinstance(d.get("marcacao"), dict) else None,
        # Etapa 4: caminhos exibidos como TEXTO (nunca link file://)
        "preparacao": {
            "pdf": str(prep.get("pdf") or ""),
            "pacote": str(prep.get("pacote") or ""),
            "idioma": str(prep.get("idioma") or ""),
            "plano": str(prep.get("plano") or ""),
            "filtro_diria": str(prep.get("filtro_diria") or ""),
            "real": bool(prep.get("real")),
            "avisos": _lista_str(prep.get("avisos")),
        } if prep else None,
        "chave": row["chave"],
        "fonte": row["fonte"],
        "empresa": row["empresa"],
        "cargo": row["cargo"],
        "localizacao": row["localizacao"] or "",
        "remoto": row["remoto"],
        "descricao": row["descricao"] or "",
        "url": row["url"],
        "url_segura": url_segura(row["url"]),
        "data_vaga": row["data_vaga"],
        "status": row["status"],
        "status_rotulo": ROTULOS_STATUS.get(row["status"], row["status"]),
        "nota": row["nota"],
        "motivo": row["motivo"] or "",
        "criado_em": row["criado_em"],
        "atualizado_em": row["atualizado_em"],
        "filtro": d.get("filtro"),
        "pontos_filtro": d.get("pontos"),
        "termos_filtro": _lista_str(d.get("termos")),
        "pontos_de_match": _lista_str(av.get("pontos_de_match")),
        "requisitos_que_faltam": _lista_str(av.get("requisitos_que_faltam")),
    }


def historico_de_status(v: dict[str, Any]) -> list[dict[str, str]]:
    """O banco guarda só o status atual; reconstruímos os passos a partir de `detalhes`."""
    passos = [{"quando": v["criado_em"], "evento": "Vaga encontrada", "detalhe": f"fonte {v['fonte']}; status inicial: nova"}]
    if v["filtro"] == "passou":
        passos.append({"quando": "", "evento": "Filtro de palavras-chave: passou",
                       "detalhe": f"+{v['pontos_filtro'] or 0} termos ({', '.join(v['termos_filtro']) or 'nenhum'})"})
    elif v["filtro"] == "bloqueada":
        passos.append({"quando": "", "evento": "Filtro de palavras-chave: bloqueada", "detalhe": v["motivo"]})
    if v["pontos_de_match"] or v["requisitos_que_faltam"] or v["nota"] is not None:
        nota = "-" if v["nota"] is None else f"{v['nota']:.0f}"
        passos.append({"quando": "", "evento": f"Avaliação do modelo: nota {nota}", "detalhe": v["motivo"]})
    if v.get("preparacao"):
        p = v["preparacao"]
        passos.append({"quando": "", "evento": "Candidatura preparada (entrada manual)",
                       "detalhe": f"currículo {p['plano']} ({p['idioma']}); filtro diria: {p['filtro_diria']}; "
                                  f"{'com' if p['real'] else 'sem'} chamada ao modelo"})
    if v.get("assistencia"):
        a = v["assistencia"]
        passos.append({"quando": a["quando"], "evento": "Copiloto assistiu o formulário (candidatar)",
                       "detalhe": f"{len(a['preenchidos'])} campo(s) preenchido(s), {len(a['pendentes'])} deixado(s) para você; "
                                  f"PDF {'anexado' if a['pdf_anexado'] else 'não anexado'}"})
    if v.get("marcacao"):
        m = v["marcacao"]
        passos.append({"quando": str(m.get("quando") or ""), "evento": f"Você marcou: {ROTULOS_STATUS.get(str(m.get('status')), str(m.get('status')))}",
                       "detalhe": ""})
    passos.append({"quando": v["atualizado_em"], "evento": f"Status atual: {v['status_rotulo']}", "detalhe": ""})
    return passos


def resumo_gasto(banco: Banco, contador: Contador) -> dict[str, Any]:
    agora = contador.agora()
    mes = banco.resumo_gasto_no_mes(agora.year, agora.month)
    teto = contador.config.teto_mensal_usd
    gasto = contador.gasto_do_mes()
    fracao = (gasto / teto) if teto > 0 else 1.0
    return {
        "mes": agora.strftime("%Y-%m"),
        "gasto_usd": gasto,
        "teto_usd": teto,
        "percentual": min(100.0, fracao * 100.0),
        "teto_atingido": gasto >= teto,
        "perto_do_teto": (gasto < teto) and fracao >= LIMITE_AVISO,
        "chamadas": mes["chamadas"],
        "tokens_entrada": mes["tokens_entrada"],
        "tokens_saida": mes["tokens_saida"],
        "total_geral": banco.resumo_gasto(),
        "modelo": contador.config.modelo,
    }


def _fmt_data(valor: str | None) -> str:
    if not valor:
        return "-"
    try:
        return datetime.fromisoformat(valor).strftime("%d/%m/%Y %H:%M")
    except ValueError:
        return str(valor)


def _fmt_usd(valor: float | None) -> str:
    return "-" if valor is None else f"US$ {valor:,.4f}"


def _fmt_int(valor: int | None) -> str:
    return "0" if valor is None else f"{int(valor):,}".replace(",", ".")


# --- app -------------------------------------------------------------------------

def criar_app(caminho_banco: Path | str = BANCO_PADRAO, caminho_custos: Path | str = CUSTOS_PADRAO,
              agora: Callable[[], datetime] | None = None) -> FastAPI:
    """`agora` é injetável nos testes (define o mês do gasto)."""
    config = carregar_custos(caminho_custos)
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES)),
        autoescape=select_autoescape(default=True, default_for_string=True),  # sempre ligado
    )
    env.filters.update(data=_fmt_data, usd=_fmt_usd, inteiro=_fmt_int)
    templates = Jinja2Templates(env=env)

    app = FastAPI(title="bot-vagas — painel local", docs_url=None, redoc_url=None, openapi_url=None)
    app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")

    @app.middleware("http")
    async def _validar_host(request: Request, call_next):
        if not host_permitido(request.headers.get("host")):
            return PlainTextResponse("Host não permitido: o painel só atende localhost.", status_code=400)
        resposta = await call_next(request)
        resposta.headers["X-Content-Type-Options"] = "nosniff"
        resposta.headers["X-Frame-Options"] = "DENY"
        resposta.headers["Referrer-Policy"] = "no-referrer"
        resposta.headers["Content-Security-Policy"] = (
            "default-src 'none'; style-src 'self'; img-src 'self'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'"
        )
        return resposta

    def abrir() -> tuple[Banco, Contador]:
        banco = Banco(caminho_banco)
        agora_dt = agora() if agora else None
        return banco, Contador(banco, config, agora=agora_dt)

    def cabecalho(banco: Banco, contador: Contador) -> dict[str, Any]:
        contagem = banco.contar_por_status()
        return {
            "contagem": {s: contagem.get(s, 0) for s in STATUS},
            "total": sum(contagem.values()),
            "gasto": resumo_gasto(banco, contador),
            "rotulos": ROTULOS_STATUS,
        }

    @app.get("/", response_class=HTMLResponse)
    def lista(request: Request, status: str = "", nota_min: str = "", ordem: str = "nota"):
        status = status if status in STATUS else ""
        try:
            nota_minima = float(nota_min) if nota_min.strip() else None
        except ValueError:
            nota_minima = None
        ordem = ordem if ordem in ORDENACOES else "nota"
        banco, contador = abrir()
        with banco:
            linhas = banco.listar(status) if status else banco.listar()
            vagas = [vaga_para_tela(r) for r in linhas]
            cab = cabecalho(banco, contador)
        if nota_minima is not None:
            vagas = [v for v in vagas if v["nota"] is not None and v["nota"] >= nota_minima]
        if ordem == "data":
            vagas.sort(key=lambda v: (v["data_vaga"] or v["criado_em"] or ""), reverse=True)
        else:  # nota desc, sem nota por último, depois mais recentes
            vagas.sort(key=lambda v: (v["nota"] is None, -(v["nota"] or 0), v["criado_em"] or ""), reverse=False)
        return templates.TemplateResponse(request, "lista.html", {
            "pagina": "lista", "vagas": vagas, "cab": cab, "filtro_status": status,
            "nota_min": "" if nota_minima is None else f"{nota_minima:g}", "ordem": ordem, "status_opcoes": STATUS,
        })

    @app.get("/vaga/{chave}", response_class=HTMLResponse)
    def detalhe(request: Request, chave: str):
        banco, contador = abrir()
        with banco:
            row = banco.buscar_por_chave(chave)
            if row is None:
                raise HTTPException(status_code=404, detail="vaga não encontrada")
            v = vaga_para_tela(row)
            cab = cabecalho(banco, contador)
        return templates.TemplateResponse(request, "detalhe.html", {
            "pagina": "detalhe", "v": v, "historico": historico_de_status(v), "cab": cab,
        })

    @app.get("/gasto", response_class=HTMLResponse)
    def gasto(request: Request):
        banco, contador = abrir()
        with banco:
            cab = cabecalho(banco, contador)
            chamadas = [dict(r) for r in banco.listar_chamadas(30)]
        return templates.TemplateResponse(request, "gasto.html", {
            "pagina": "gasto", "g": cab["gasto"], "chamadas": chamadas, "cab": cab,
        })

    return app


def servir(porta: int = 8765, caminho_banco: Path | str = BANCO_PADRAO,
           caminho_custos: Path | str = CUSTOS_PADRAO, host: str = "127.0.0.1") -> None:
    """Sobe o uvicorn. `host` é fixo em loopback por desenho; não aceite 0.0.0.0."""
    import uvicorn

    if host not in ("127.0.0.1", "localhost", "::1"):
        raise ValueError("o painel só pode escutar em loopback (127.0.0.1)")
    print(f"painel em http://127.0.0.1:{porta}/  (somente leitura; Ctrl+C para parar)", flush=True)
    uvicorn.run(criar_app(caminho_banco, caminho_custos), host=host, port=porta, log_level="warning")
