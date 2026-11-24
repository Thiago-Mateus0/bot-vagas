"""CLI do bot-vagas.

Uso (Etapa 1, comportamento preservado quando não há subcomando):
    .venv/bin/python -m bot_vagas.cli                 # PDFs PT e EN em saida/
    .venv/bin/python -m bot_vagas.cli --idioma en
    bot-vagas curriculo --idioma en                   # mesma coisa, explícito

Etapa 2 (somente leitura; nada se candidata):
    bot-vagas buscar      # REDE: consulta as APIs públicas de fontes.yaml e salva em vagas.db
    bot-vagas filtrar     # fase A (palavras-chave), sem rede
    bot-vagas filtrar --refazer   # reaplica a fase A às vagas sem nota (quando as regras mudam)
    bot-vagas avaliar     # fase B (nota do modelo). EXIGE --real para chamar a API paga
    bot-vagas listar      # tabela: empresa, cargo, nota, status, link

Etapa 3 (painel web local, somente leitura, só em 127.0.0.1):
    bot-vagas painel [--porta 8765] [--banco vagas.db]

Etapa 4 (entrada manual; o bot PREPARA, Thiago envia; sem login, sem envio automático):
    bot-vagas preparar <url>                       # carrega UMA página, PDF neutro + pacote .md
    bot-vagas preparar <url> --real                # + nota e currículo adaptado (API paga)
    bot-vagas preparar --arquivo vaga.txt --empresa X --cargo Y [--url U]   # texto colado

Copiloto Gupy (SEMI-AUTOMÁTICO: Thiago faz login e clica em tudo; o bot só preenche/anexa/destaca):
    bot-vagas login-gupy                              # Chromium visível para VOCÊ logar; nada é lido
    bot-vagas candidatar <url|chave> [--pdf x.pdf] [--idioma pt|en]   # assiste o formulário até fechar
    bot-vagas marcar <chave|url> enviada|desistiu|em_candidatura      # você diz quando enviou

LinkedIn (AUTOMÁTICO, por decisão do Thiago; risco para a conta aceito — ver docs/DECISOES.md):
    bot-vagas login-linkedin                          # Chromium visível para VOCÊ logar; nada é lido
    bot-vagas aplicar-linkedin [--sem-enviar] [--max N]   # busca, filtra e envia Candidatura Simplificada

Operação:
    bot-vagas parar [motivo] / bot-vagas retomar    # botão PARAR (arquivo PARAR na raiz do projeto)
    bot-vagas rodar                                 # rodada diária: buscar + filtrar (usada pelo timer)
    bot-vagas gerar-timer [--hora 14:00]            # gera (não instala) o timer do systemd --user
    bot-vagas testar-notificacao                    # manda um push de teste pelo ntfy (se configurado)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .banco import STATUS_MANUAIS
from . import copiloto as copiloto_mod
from .copiloto import PASTA_PERFIL_PADRAO
from .curriculo import CurriculoNaoCabeEmUmaPagina, gerar_curriculo
from .perfil import PERFIL_PADRAO, RAIZ_PROJETO, carregar_perfil
from .plano import plano_neutro

SAIDA_PADRAO = RAIZ_PROJETO / "saida"
SUBCOMANDOS = ("curriculo", "buscar", "filtrar", "avaliar", "listar", "painel", "preparar",
               "login-gupy", "candidatar", "marcar", "parar", "retomar", "rodar", "gerar-timer", "testar-notificacao",
               "login-linkedin", "aplicar-linkedin", "diagnosticar-linkedin")
# comandos que fazem trabalho (rede, navegador, API, banco): não começam com o PARAR ligado
COMANDOS_BLOQUEADOS_POR_PARAR = ("buscar", "filtrar", "avaliar", "preparar", "login-gupy", "candidatar", "rodar",
                                 "login-linkedin", "aplicar-linkedin", "diagnosticar-linkedin")


# --- Etapa 1 ---------------------------------------------------------------

def _parser_curriculo(p: argparse.ArgumentParser) -> None:
    p.add_argument("--perfil", type=Path, default=PERFIL_PADRAO, help="caminho do perfil.yaml")
    p.add_argument("--idioma", choices=["pt", "en", "ambos"], default="ambos")
    p.add_argument("--saida", type=Path, default=SAIDA_PADRAO, help="pasta de saída")


def cmd_curriculo(args: argparse.Namespace) -> int:
    perfil = carregar_perfil(args.perfil)
    idiomas = ["pt", "en"] if args.idioma == "ambos" else [args.idioma]
    codigo = 0
    for idioma in idiomas:
        destino = args.saida / f"curriculo-thiago-mateus-{idioma}.pdf"
        try:
            gerar_curriculo(perfil, plano_neutro(perfil, idioma), destino)
        except CurriculoNaoCabeEmUmaPagina as e:
            print(f"[{idioma}] ERRO: {e}", file=sys.stderr)
            codigo = 1
        else:
            print(f"[{idioma}] gerado: {destino}")
    return codigo


# --- Etapa 2 ---------------------------------------------------------------

def _opcoes_comuns(p: argparse.ArgumentParser) -> None:
    from .banco import BANCO_PADRAO

    p.add_argument("--banco", type=Path, default=BANCO_PADRAO, help="arquivo SQLite (padrão vagas.db)")


def cmd_buscar(args: argparse.Namespace) -> int:
    from .banco import Banco
    from .fontes.config import carregar_fontes
    from .fontes.http import ClienteHTTPReal
    from .pipeline import buscar

    fontes = carregar_fontes(args.fontes)
    if not fontes:
        print("fontes.yaml não tem nenhuma fonte configurada.", file=sys.stderr)
        return 1
    print(f"consultando {len(fontes)} fonte(s) via APIs públicas (sem login, sem dados pessoais)...")
    with Banco(args.banco) as banco:
        total = buscar(fontes, ClienteHTTPReal(), banco)
    print(f"total: {total['baixadas']} baixadas, {total['novas']} novas, {total['fontes_com_erro']} fonte(s) com erro, "
          f"{total['alarmes']} alarme(s) de formato/bloqueio")
    if total["alarmes"]:
        print("ATENÇÃO: houve alarme de mudança de formato ou bloqueio (ver mensagens acima); "
              "nada foi contornado. Código de saída 1.", file=sys.stderr)
        return 1
    return 0


def cmd_filtrar(args: argparse.Namespace) -> int:
    from .banco import Banco
    from .filtro import carregar_filtros
    from .pipeline import filtrar, refiltrar

    with Banco(args.banco) as banco:
        if args.refazer:
            refiltrar(banco, carregar_filtros(args.filtros))
        else:
            filtrar(banco, carregar_filtros(args.filtros))
    return 0


MENSAGEM_SEM_REAL = (
    "RECUSADO: `avaliar` chama a API paga da Anthropic (claude-haiku-5-5) e ainda não foi "
    "autorizada nenhuma chamada paga. Para autorizar, rode com a flag explícita --real "
    "(depois de conferir custos.yaml, o teto na conta da Anthropic e o prompt em avaliador.py)."
)


def cmd_avaliar(args: argparse.Namespace) -> int:
    from .banco import Banco
    from .filtro import carregar_filtros
    from .gasto import Contador, carregar_custos
    from .pipeline import avaliar, pendentes_de_avaliacao

    if not args.real:
        with Banco(args.banco) as banco:
            n = len(pendentes_de_avaliacao(banco))
        print(MENSAGEM_SEM_REAL, file=sys.stderr)
        print(f"({n} vaga(s) aguardando avaliação; nenhuma chamada foi feita)", file=sys.stderr)
        return 2

    custos = carregar_custos(args.custos)
    client = args.client_factory() if args.client_factory else _cliente_anthropic_real()
    perfil = carregar_perfil(args.perfil)
    with Banco(args.banco) as banco:
        contador = Contador(banco, custos)
        print(f"gasto no mês até agora: US$ {contador.gasto_do_mes():.4f} (teto US$ {custos.teto_mensal_usd:.2f})")
        total = avaliar(perfil, banco, carregar_filtros(args.filtros), client, contador, limite=args.limite)
        print(f"avaliadas: {total['aprovadas']} aprovadas, {total['abaixo_do_corte']} abaixo do corte, "
              f"{total['erros']} erros, {total['bloqueadas_por_teto']} não avaliadas por teto; "
              f"gasto no mês: US$ {contador.gasto_do_mes():.4f}")
    return 0


def _cliente_anthropic_real():
    """Único lugar que instancia o cliente real. Só é chamado com --real.
    O SDK lê a credencial do ambiente por conta própria; este código não lê ANTHROPIC_API_KEY."""
    import anthropic

    return anthropic.Anthropic()


def cmd_listar(args: argparse.Namespace) -> int:
    from .banco import Banco

    with Banco(args.banco) as banco:
        linhas = banco.listar(args.status) if args.status else banco.listar()
        contagem = banco.contar_por_status()
    if not linhas:
        print("nenhuma vaga no banco.")
        return 0
    larg_emp, larg_cargo = 22, 40

    def corta(s: str, n: int) -> str:
        s = s or ""
        return s if len(s) <= n else s[: n - 1] + "…"

    print(f"{'EMPRESA':<{larg_emp}} {'CARGO':<{larg_cargo}} {'NOTA':>4} {'STATUS':<16} LINK")
    for r in linhas:
        nota = "-" if r["nota"] is None else f"{r['nota']:.0f}"
        print(f"{corta(r['empresa'], larg_emp):<{larg_emp}} {corta(r['cargo'], larg_cargo):<{larg_cargo}} {nota:>4} {r['status']:<16} {r['url']}")
    print("\n" + ", ".join(f"{k}: {v}" for k, v in sorted(contagem.items())))
    return 0


# --- Etapa 3 ---------------------------------------------------------------

def cmd_painel(args: argparse.Namespace) -> int:
    from .painel import servir

    servir(porta=args.porta, caminho_banco=args.banco, caminho_custos=args.custos)
    return 0


# --- Etapa 4 ---------------------------------------------------------------

MENSAGEM_PREPARAR_SEM_REAL = (
    "sem --real: nenhuma chamada à API será feita (PDF neutro). Para pedir nota e currículo "
    "adaptado ao modelo pago, repita com --real."
)


def cmd_preparar(args: argparse.Namespace) -> int:
    from .banco import Banco
    from .filtro import carregar_filtros
    from .gasto import Contador, carregar_custos
    from .pagina import ErroPagina, carregar_pagina
    from .preparar import VagaJaEnviada, preparar, vaga_manual
    from .respostas import carregar_respostas

    if not args.url_pos and not args.arquivo:
        print("informe a URL da vaga ou --arquivo vaga.txt", file=sys.stderr)
        return 2
    if args.url_pos and args.arquivo:
        print("use OU a URL posicional OU --arquivo (com --url para registrar o link)", file=sys.stderr)
        return 2

    if args.arquivo:
        try:
            texto = Path(args.arquivo).read_text(encoding="utf-8")
        except OSError as e:
            print(f"não consegui ler {args.arquivo}: {e}", file=sys.stderr)
            return 1
        if not texto.strip():
            print(f"{args.arquivo} está vazio", file=sys.stderr)
            return 1
        primeira = next((l.strip() for l in texto.splitlines() if l.strip()), "")
        cargo = args.cargo or primeira
        empresa = args.empresa or ""
        url = args.url or ""
        if not args.cargo:
            print(f"aviso: --cargo não informado; usando a primeira linha do arquivo: {cargo!r}")
        if not empresa:
            print("aviso: --empresa não informada; o pacote sairá como 'empresa-nao-informada'")
        vaga = vaga_manual(empresa=empresa, cargo=cargo, url=url, localizacao=args.localizacao or "", descricao=texto)
    else:
        print(f"carregando UMA página (Playwright headless, sem login): {args.url_pos}")
        try:
            pag = carregar_pagina(args.url_pos, fetch=args.fetch)
        except ErroPagina as e:
            print(f"RECUSADO/FALHOU: {e}", file=sys.stderr)
            return 1
        for a in pag.avisos:
            print(f"  ({a})")
        if pag.url_final != pag.url:
            print(f"  (redirecionada para {pag.url_final})")
        vaga = vaga_manual(
            empresa=args.empresa or pag.empresa, cargo=args.cargo or pag.titulo,
            url=args.url or args.url_pos, localizacao=args.localizacao or pag.localizacao, descricao=pag.descricao,
        )
        print(f"extraído: empresa={vaga.empresa!r} cargo={vaga.cargo!r} localização={vaga.localizacao!r} "
              f"descrição={len(vaga.descricao)} caracteres")

    perfil = carregar_perfil(args.perfil)
    filtros = carregar_filtros(args.filtros)
    respostas = carregar_respostas(args.respostas)
    custos = carregar_custos(args.custos)
    if args.real:
        client = args.client_factory() if args.client_factory else _cliente_anthropic_real()
    else:
        client = None
        print(MENSAGEM_PREPARAR_SEM_REAL)

    with Banco(args.banco) as banco:
        contador = Contador(banco, custos)
        if args.real:
            print(f"gasto no mês até agora: US$ {contador.gasto_do_mes():.4f} (teto US$ {custos.teto_mensal_usd:.2f})")
        try:
            r = preparar(vaga, perfil=perfil, banco=banco, filtros=filtros, respostas=respostas, saida=args.saida,
                         client=client, contador=contador, idioma=args.idioma, permitir_enviada=args.forcar)
        except VagaJaEnviada as e:
            print(f"RECUSADO: {e}", file=sys.stderr)
            return 2
        if args.real:
            print(f"gasto no mês após esta preparação: US$ {contador.gasto_do_mes():.4f}")
    print("\nPRONTO (nada foi enviado). Próximo passo é seu: abra o link, anexe o PDF e use o pacote:")
    print(f"  PDF:    {r.pdf}\n  pacote: {r.pacote}")
    return 0


# --- Copiloto Gupy -------------------------------------------------------------

def _cfg_copiloto(args) -> dict:
    """Produção: headed, só gupy.io, perfil em ~/.local/share/bot-vagas/navegador. Testes injetam outra coisa."""
    cfg = dict(args.copiloto_cfg or {})
    cfg.setdefault("headless", False)
    cfg.setdefault("permitir_loopback", False)
    cfg.setdefault("max_passos", None)
    cfg.setdefault("intervalo", 1.0)
    cfg.setdefault("pasta_perfil", getattr(args, "perfil_navegador", None) or copiloto_mod.PASTA_PERFIL_PADRAO)
    cfg.setdefault("pasta_logs", copiloto_mod.PASTA_LOGS_PADRAO)
    return cfg


CODIGO_PERFIL_EM_USO = 3


class _ignorando_ctrl_c:
    """Ignora SIGINT enquanto grava o resumo (só na thread principal; em threads de teste não faz nada)."""

    def __enter__(self):
        import signal
        import threading

        self._antigo = None
        if threading.current_thread() is threading.main_thread():
            self._antigo = signal.signal(signal.SIGINT, signal.SIG_IGN)
        return self

    def __exit__(self, *exc):
        import signal

        if self._antigo is not None:
            signal.signal(signal.SIGINT, self._antigo)
        return False


def _config_notificacao(args):
    from .notificacao import ConfigNotificacaoInvalida, DESLIGADA, carregar_notificacao

    try:
        return carregar_notificacao(getattr(args, "notificacao", None) or _caminho_notificacao())
    except ConfigNotificacaoInvalida as e:
        print(f"(aviso: notificacao.yaml inválido, notificação desligada: {e})", file=sys.stderr)
        return DESLIGADA


def _caminho_notificacao() -> Path:
    from .notificacao import NOTIFICACAO_PADRAO

    return NOTIFICACAO_PADRAO


def cmd_login_gupy(args: argparse.Namespace) -> int:
    from .copiloto import PerfilEmUso, login_gupy
    from .parada import parar_solicitado

    cfg = _cfg_copiloto(args)
    try:
        return login_gupy(cfg["pasta_perfil"], headless=cfg["headless"], max_segundos=cfg.get("max_segundos"),
                          url=cfg.get("url_login") or "https://portal.gupy.io/", deve_parar=parar_solicitado)
    except PerfilEmUso as e:
        print(str(e), file=sys.stderr)
        return CODIGO_PERFIL_EM_USO


def cmd_candidatar(args: argparse.Namespace) -> int:
    import json

    from .banco import Banco
    from .copiloto import (MENSAGEM_PERFIL_EM_USO, Alvos, Copiloto, LogCampos, PerfilEmUso, assistir, host_da_url,
                           perfil_em_uso, validar_url_candidatura)
    from .notificacao import Notificador
    from .parada import parar_solicitado
    from .filtro import carregar_filtros
    from .fontes import Vaga
    from .gasto import Contador, carregar_custos
    from .pagina import ErroPagina, URLRecusada, carregar_pagina
    from .preparar import VagaJaEnviada, preparar, vaga_manual
    from .respostas import carregar_respostas

    cfg = _cfg_copiloto(args)
    alvos = Alvos(permitir_loopback=bool(cfg["permitir_loopback"]))

    with Banco(args.banco) as banco:
        row = banco.buscar(args.alvo)
        url = row["url"] if row is not None else args.alvo
        try:
            url = validar_url_candidatura(url, alvos)
        except URLRecusada as e:
            print(f"RECUSADO: {e}", file=sys.stderr)
            return 2
        if row is not None and row["status"] == "enviada" and not args.forcar:
            print(f"RECUSADO: esta vaga já está marcada como ENVIADA (chave {row['chave'][:10]}…). "
                  "Para assistir de novo mesmo assim, repita com --forcar.", file=sys.stderr)
            return 2

        # 1) PDF: o informado > o da preparação registrada > preparar agora SEM --real (nenhuma chamada paga)
        pdf = args.pdf
        detalhes = {}
        if row is not None and row["detalhes"]:
            try:
                detalhes = json.loads(row["detalhes"]) or {}
            except ValueError:
                detalhes = {}
        if pdf is None and isinstance(detalhes.get("preparacao"), dict):
            cand = Path(str(detalhes["preparacao"].get("pdf") or ""))
            if cand.exists():
                pdf = cand
                print(f"usando o PDF da preparação registrada: {pdf}")
        # vaga: do banco, ou descoberta agora (página pública via Playwright headless; senão, pelo que foi informado)
        if row is not None:
            vaga = Vaga(id_fonte=row["id_fonte"], fonte=row["fonte"], empresa=row["empresa"], cargo=row["cargo"],
                        localizacao=row["localizacao"] or "", remoto=None, descricao=row["descricao"] or "", url=row["url"])
        else:
            vaga = None
            try:
                pag = carregar_pagina(url, fetch=args.fetch)
                vaga = vaga_manual(empresa=args.empresa or pag.empresa, cargo=args.cargo or pag.titulo, url=url,
                                   localizacao=pag.localizacao, descricao=pag.descricao)
            except ErroPagina as e:
                print(f"  (não consegui ler a página sem login: {str(e).splitlines()[0][:160]})")
            if vaga is None:
                host = host_da_url(url)
                empresa = args.empresa or (host.split(".")[0] if host.endswith("gupy.io") and host.count(".") >= 2 else "")
                cargo = args.cargo or (url.rstrip("/").split("/")[-1] or "vaga")
                vaga = vaga_manual(empresa=empresa, cargo=cargo, url=url)
            if pdf is not None:  # --pdf informado: só registra a vaga no banco (sem preparar)
                banco.salvar_vagas([vaga])
                row = banco.buscar_por_url(vaga.url)
        if pdf is None:
            print("vaga ainda não preparada: preparando agora SEM --real (PDF neutro; nenhuma chamada à API).")
            perfil = carregar_perfil(args.perfil)
            contador = Contador(banco, carregar_custos(args.custos))
            try:
                r = preparar(vaga, perfil=perfil, banco=banco, filtros=carregar_filtros(args.filtros),
                             respostas=carregar_respostas(args.respostas), saida=args.saida, client=None,
                             contador=contador, idioma=args.idioma, permitir_enviada=args.forcar)
            except VagaJaEnviada as e:
                print(f"RECUSADO: {e}", file=sys.stderr)
                return 2
            pdf = r.pdf
            row = banco.buscar_por_chave(r.chave)
            detalhes = json.loads(row["detalhes"]) if row["detalhes"] else {}
        if pdf is None or not Path(pdf).exists():
            print(f"PDF não encontrado: {pdf}", file=sys.stderr)
            return 1
        chave = row["chave"]
        if perfil_em_uso(cfg["pasta_perfil"]):  # antes de mexer no status
            print(MENSAGEM_PERFIL_EM_USO, file=sys.stderr)
            return CODIGO_PERFIL_EM_USO
        status_anterior = row["status"]
        banco.atualizar_status(chave, "em_candidatura")
        print(f"status: em_candidatura (chave {chave}). PDF a anexar: {pdf}")

        # 2) assistência (Chromium visível, perfil persistente). Valores pessoais só vão para a página.
        respostas = carregar_respostas(args.respostas)
        log_campos = None
        if not args.sem_log:
            try:
                log_campos = LogCampos(cfg["pasta_logs"], respostas)
                print(f"log de depuração (local, 0600, sem valores pessoais): {log_campos.caminho}  (--sem-log desliga)")
            except OSError as e:
                print(f"(aviso: não consegui criar o log de depuração: {e}; seguindo sem log)", file=sys.stderr)
        copiloto = Copiloto(respostas, pdf, alvos=alvos, log_campos=log_campos,
                            notificador=Notificador(_config_notificacao(args)))
        registro = None
        gravar = True
        try:
            registro = assistir(url, copiloto, pasta_perfil=cfg["pasta_perfil"], headless=cfg["headless"],
                                intervalo=cfg["intervalo"], max_passos=cfg["max_passos"], deve_parar=parar_solicitado,
                                ao_ciclo=cfg.get("ao_ciclo"))
        except PerfilEmUso as e:
            gravar = False  # nada aconteceu: volta o status e não grava assistência
            banco.atualizar_status(chave, status_anterior)
            print(str(e), file=sys.stderr)
            return CODIGO_PERFIL_EM_USO
        except BaseException as e:  # assistir não deveria deixar nada escapar; mesmo assim o resumo é gravado
            copiloto.registro.encerramento = copiloto.registro.encerramento or f"interrompido ({type(e).__name__})"
            raise
        finally:
            if log_campos is not None:
                log_campos.fechar()
            if gravar:
                # 3) resumo e histórico (sem valores pessoais) — SEMPRE, inclusive após Ctrl+C/janela fechada.
                #    Ctrl+C extra durante a gravação é ignorado para não perder o registro.
                with _ignorando_ctrl_c():
                    print(copiloto.resumo(), flush=True)
                    historico = (detalhes.get("historico_assistencia")
                                 if isinstance(detalhes.get("historico_assistencia"), list) else [])
                    if detalhes.get("assistencia"):
                        historico.append(detalhes["assistencia"])
                    detalhes["assistencia"] = (registro or copiloto.registro).para_json()
                    detalhes["historico_assistencia"] = historico[-10:]
                    banco.atualizar_status(chave, "em_candidatura", detalhes=json.dumps(detalhes, ensure_ascii=False))
    print(f"\nSe você enviou a candidatura, registre: bot-vagas marcar {chave} enviada")
    return 0


def cmd_marcar(args: argparse.Namespace) -> int:
    import json
    from datetime import datetime, timezone

    from .banco import Banco

    with Banco(args.banco) as banco:
        row = banco.buscar(args.alvo)
        if row is None:
            print(f"vaga não encontrada no banco: {args.alvo}", file=sys.stderr)
            return 1
        try:
            detalhes = json.loads(row["detalhes"]) if row["detalhes"] else {}
        except ValueError:
            detalhes = {}
        if not isinstance(detalhes, dict):
            detalhes = {}
        detalhes["marcacao"] = {"status": args.status, "quando": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                "anterior": row["status"]}
        banco.atualizar_status(row["chave"], args.status, detalhes=json.dumps(detalhes, ensure_ascii=False))
    print(f"{row['empresa']} / {row['cargo']}: {row['status']} -> {args.status}")
    return 0


# --- LinkedIn (automático) -----------------------------------------------------------

def cmd_login_linkedin(args: argparse.Namespace) -> int:
    from .copiloto import PerfilEmUso
    from .linkedin import URL_LOGIN, login_linkedin
    from .parada import parar_solicitado

    cfg = _cfg_copiloto(args)
    try:
        return login_linkedin(cfg["pasta_perfil"], headless=cfg["headless"], max_segundos=cfg.get("max_segundos"),
                              url=cfg.get("url_login") or URL_LOGIN, deve_parar=parar_solicitado)
    except PerfilEmUso as e:
        print(str(e), file=sys.stderr)
        return CODIGO_PERFIL_EM_USO


def cmd_diagnosticar_linkedin(args: argparse.Namespace) -> int:
    from .copiloto import PerfilEmUso
    from .linkedin import carregar_config, diagnosticar

    cfg = _cfg_copiloto(args)
    try:
        return diagnosticar(carregar_config(args.linkedin), cfg["pasta_perfil"], headless=cfg["headless"])
    except PerfilEmUso as e:
        print(str(e), file=sys.stderr)
        return CODIGO_PERFIL_EM_USO


def cmd_aplicar_linkedin(args: argparse.Namespace) -> int:
    from dataclasses import replace

    from .banco import Banco
    from .copiloto import LogCampos, PerfilEmUso, perfil_em_uso
    from .filtro import carregar_filtros
    from .linkedin import aplicar, carregar_config
    from .notificacao import Notificador
    from .parada import parar_solicitado
    from .respostas import carregar_respostas

    cfg = _cfg_copiloto(args)
    config = carregar_config(args.linkedin)
    if cfg.get("base_linkedin"):  # só testes
        config = replace(config, base=cfg["base_linkedin"])
    if cfg.get("pausas_zero"):  # só testes
        config = replace(config, pausa_acao=(0.0, 0.0), pausa_vaga=(0.0, 0.0))
    if perfil_em_uso(cfg["pasta_perfil"]):
        print(MENSAGEM_PERFIL_EM_USO_CLI, file=sys.stderr)
        return CODIGO_PERFIL_EM_USO
    respostas = carregar_respostas(args.respostas)
    if respostas is None:
        print("aviso: respostas.yaml não existe; toda pergunta obrigatória fará o bot desistir da vaga "
              "(copie respostas.example.yaml).", file=sys.stderr)

    pdf = args.pdf
    if pdf is None:  # currículo neutro, sem API paga; só é anexado se o formulário pedir um arquivo
        try:
            from .curriculo import gerar_curriculo
            from .plano import plano_neutro

            perfil = carregar_perfil(args.perfil)
            pdf = args.saida / "curriculo-linkedin-pt.pdf"
            gerar_curriculo(perfil, plano_neutro(perfil, "pt"), pdf)
        except Exception as e:
            pdf = None
            print(f"(aviso: sem PDF do currículo para anexar: {type(e).__name__}; o LinkedIn usará o currículo salvo "
                  "na sua conta)", file=sys.stderr)

    log_campos = None
    if not args.sem_log:
        try:
            log_campos = LogCampos(cfg["pasta_logs"], respostas, prefixo="linkedin")
            print(f"log de depuração (local, 0600, sem valores pessoais): {log_campos.caminho}")
        except OSError:
            pass
    with Banco(args.banco) as banco:
        try:
            resumo = aplicar(config, respostas, banco=banco, filtros=carregar_filtros(args.filtros), pdf=pdf,
                             pasta_perfil=cfg["pasta_perfil"], headless=cfg["headless"], enviar=not args.sem_enviar,
                             maximo=args.max, deve_parar=parar_solicitado, log_campos=log_campos,
                             notificador=Notificador(_config_notificacao(args)),
                             permitir_loopback=bool(cfg["permitir_loopback"]))
        except PerfilEmUso as e:
            print(str(e), file=sys.stderr)
            return CODIGO_PERFIL_EM_USO
        finally:
            if log_campos is not None:
                log_campos.fechar()
    print(resumo.texto(), flush=True)
    return 0


MENSAGEM_PERFIL_EM_USO_CLI = ("Já existe um navegador usando o perfil do bot (feche a janela do login-linkedin/"
                              "login-gupy ou de outro comando) e tente de novo.")


# --- parada, rodada diária, agendamento e notificação ---------------------------

def cmd_parar(args: argparse.Namespace) -> int:
    from .parada import solicitar_parada

    caminho = solicitar_parada(" ".join(args.motivo or []))
    print(f"PARAR ligado: {caminho}. Execuções em andamento param no próximo ponto seguro; novas não começam.")
    print("Para liberar: bot-vagas retomar")
    return 0


def cmd_retomar(args: argparse.Namespace) -> int:
    from .parada import caminho_parar, retomar

    if retomar():
        print(f"PARAR removido ({caminho_parar()}). O bot pode rodar de novo.")
    else:
        print("não havia PARAR ligado; nada a fazer.")
    return 0


def cmd_rodar(args: argparse.Namespace) -> int:
    """Rodada diária (usada pelo timer): buscar + filtrar. Nenhuma chamada paga, nada é enviado."""
    from .banco import Banco
    from .filtro import carregar_filtros
    from .fontes.config import carregar_fontes
    from .fontes.http import ClienteHTTPReal
    from .notificacao import Notificador
    from .parada import ParadaSolicitada
    from .pipeline import buscar, filtrar

    notificador = Notificador(_config_notificacao(args))
    try:
        fontes = carregar_fontes(args.fontes)
        http = args.http_factory() if args.http_factory else ClienteHTTPReal()
        print(f"rodada: consultando {len(fontes)} fonte(s) (sem login, sem dados pessoais)...")
        with Banco(args.banco) as banco:
            total = buscar(fontes, http, banco)
            filtro = filtrar(banco, carregar_filtros(args.filtros))
    except ParadaSolicitada:
        notificador.notificar("parado")
        raise
    except Exception:
        notificador.notificar("erro")
        raise
    print(f"rodada concluída: {total['baixadas']} baixadas, {total['novas']} novas, "
          f"{filtro['passaram']} passaram no filtro, {total['alarmes']} alarme(s)")
    if fontes and total["fontes_com_erro"] >= len(fontes):
        notificador.notificar("erro")
        print("ERRO: nenhuma fonte respondeu (sem internet? site fora do ar?). Código de saída 1.", file=sys.stderr)
        return 1
    if total["fontes_com_erro"]:
        print(f"aviso: {total['fontes_com_erro']} fonte(s) com erro de rede; as demais foram lidas.", file=sys.stderr)
    if total["alarmes"]:
        notificador.notificar("alarme")
        print("ATENÇÃO: houve alarme de bloqueio/mudança de formato; nada foi contornado. Código de saída 1.",
              file=sys.stderr)
        return 1
    notificador.notificar("rodada", n=filtro["passaram"])
    print("próximo passo (seu): bot-vagas listar --status nova  →  escolher  →  bot-vagas candidatar <url>")
    return 0


def cmd_gerar_timer(args: argparse.Namespace) -> int:
    from .agendamento import INSTRUCOES, escrever_unidades

    python = args.python
    if python is None:
        venv = RAIZ_PROJETO / ".venv" / "bin" / "python"
        python = venv if venv.exists() else Path(sys.executable)
    try:
        arquivos = escrever_unidades(args.saida, raiz=RAIZ_PROJETO, python=python, hora=args.hora)
    except ValueError as e:
        print(f"RECUSADO: {e}", file=sys.stderr)
        return 2
    for a in arquivos:
        print(f"gerado: {a}")
    print(INSTRUCOES.format(destino=args.saida.resolve()))
    return 0


def cmd_testar_notificacao(args: argparse.Namespace) -> int:
    from .notificacao import Notificador

    config = _config_notificacao(args)
    if not config.ligada:
        print("notificação DESLIGADA: crie notificacao.yaml a partir de notificacao.example.yaml e preencha `topico`.")
        return 1
    ok = Notificador(config).notificar("teste")
    print("notificação de teste enviada; confira o celular." if ok else "falhou (veja o aviso acima).")
    return 0 if ok else 1


# --- montagem --------------------------------------------------------------

def construir_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bot-vagas", description="bot-vagas: busca, filtro, currículo e copiloto de candidaturas (nunca envia sozinho).")
    sub = parser.add_subparsers(dest="comando")

    p = sub.add_parser("curriculo", help="gera o currículo base em PDF (Etapa 1)")
    _parser_curriculo(p)
    p.set_defaults(func=cmd_curriculo)

    p = sub.add_parser("buscar", help="REDE: baixa vagas das APIs públicas e salva no SQLite")
    _opcoes_comuns(p)
    p.add_argument("--fontes", type=Path, default=RAIZ_PROJETO / "fontes.yaml")
    p.set_defaults(func=cmd_buscar)

    p = sub.add_parser("filtrar", help="fase A: filtro por palavras-chave")
    _opcoes_comuns(p)
    p.add_argument("--filtros", type=Path, default=RAIZ_PROJETO / "filtros.yaml")
    p.add_argument("--refazer", action="store_true",
                   help="reaplica a fase A a todas as vagas 'nova'/'bloqueada' sem nota (regras mudaram); "
                        "vagas com nota do modelo não são tocadas")
    p.set_defaults(func=cmd_filtrar)

    p = sub.add_parser("avaliar", help="fase B: nota do modelo (exige --real)")
    _opcoes_comuns(p)
    p.add_argument("--filtros", type=Path, default=RAIZ_PROJETO / "filtros.yaml")
    p.add_argument("--custos", type=Path, default=RAIZ_PROJETO / "custos.yaml")
    p.add_argument("--perfil", type=Path, default=PERFIL_PADRAO)
    p.add_argument("--limite", type=int, default=None, help="avaliar no máximo N vagas")
    p.add_argument("--real", action="store_true", help="AUTORIZA chamadas pagas à API da Anthropic")
    p.set_defaults(func=cmd_avaliar, client_factory=None)

    p = sub.add_parser("listar", help="tabela das vagas no banco")
    _opcoes_comuns(p)
    p.add_argument("--status", nargs="*", help="filtrar por status (nova bloqueada avaliada abaixo_do_corte aprovada preparada em_candidatura enviada desistiu)")
    p.set_defaults(func=cmd_listar)

    p = sub.add_parser("painel", help="painel web local somente leitura em http://127.0.0.1:PORTA (Etapa 3)")
    _opcoes_comuns(p)
    p.add_argument("--custos", type=Path, default=RAIZ_PROJETO / "custos.yaml")
    p.add_argument("--porta", type=int, default=8765, help="porta TCP em 127.0.0.1 (padrão 8765)")
    p.set_defaults(func=cmd_painel)

    p = sub.add_parser("preparar", help="Etapa 4: prepara a candidatura de UMA vaga escolhida por você (nada é enviado)")
    _opcoes_comuns(p)
    p.add_argument("url_pos", nargs="?", metavar="URL", help="link da vaga (http/https; carrega uma única página)")
    p.add_argument("--arquivo", type=Path, help="texto da vaga colado em um arquivo (quando a página exige login)")
    p.add_argument("--empresa", help="nome da empresa (obrigatório na prática com --arquivo)")
    p.add_argument("--cargo", help="título da vaga")
    p.add_argument("--url", help="link da vaga para registrar (com --arquivo) ou sobrescrever")
    p.add_argument("--localizacao", help="localização da vaga, se quiser informar")
    p.add_argument("--idioma", choices=["pt", "en"], default=None, help="força o idioma do currículo")
    p.add_argument("--saida", type=Path, default=SAIDA_PADRAO)
    p.add_argument("--perfil", type=Path, default=PERFIL_PADRAO)
    p.add_argument("--filtros", type=Path, default=RAIZ_PROJETO / "filtros.yaml")
    p.add_argument("--custos", type=Path, default=RAIZ_PROJETO / "custos.yaml")
    p.add_argument("--respostas", type=Path, default=RAIZ_PROJETO / "respostas.yaml")
    p.add_argument("--real", action="store_true", help="AUTORIZA chamadas pagas (nota + currículo adaptado)")
    p.add_argument("--forcar", action="store_true", help="prepara de novo mesmo se a vaga já estiver marcada como enviada")
    p.set_defaults(func=cmd_preparar, client_factory=None, fetch=None)

    # --- copiloto Gupy ---
    p = sub.add_parser("login-gupy", help="abre um Chromium VISÍVEL com perfil persistente para VOCÊ fazer login na Gupy")
    p.add_argument("--perfil-navegador", type=Path, default=None, help=f"pasta do perfil (padrão {PASTA_PERFIL_PADRAO})")
    p.set_defaults(func=cmd_login_gupy, copiloto_cfg=None)

    p = sub.add_parser("candidatar", help="copiloto: abre a vaga da Gupy no Chromium visível e preenche/anexa/destaca; NUNCA clica nem envia")
    _opcoes_comuns(p)
    p.add_argument("alvo", metavar="URL|CHAVE", help="link da vaga na Gupy (*.gupy.io) ou chave da vaga no banco")
    p.add_argument("--pdf", type=Path, default=None, help="PDF do currículo a anexar (padrão: o da preparação, ou prepara agora SEM --real)")
    p.add_argument("--idioma", choices=["pt", "en"], default=None, help="idioma do currículo se precisar preparar agora")
    p.add_argument("--empresa", help="nome da empresa (se a página não puder ser lida sem login)")
    p.add_argument("--cargo", help="título da vaga (idem)")
    p.add_argument("--forcar", action="store_true", help="assiste mesmo se a vaga já estiver marcada como enviada")
    p.add_argument("--sem-log", action="store_true",
                   help="não grava o log local de depuração (~/.local/share/bot-vagas/logs/candidatar-*.jsonl)")
    p.add_argument("--perfil-navegador", type=Path, default=None, help=f"pasta do perfil (padrão {PASTA_PERFIL_PADRAO})")
    p.add_argument("--saida", type=Path, default=SAIDA_PADRAO)
    p.add_argument("--perfil", type=Path, default=PERFIL_PADRAO)
    p.add_argument("--filtros", type=Path, default=RAIZ_PROJETO / "filtros.yaml")
    p.add_argument("--custos", type=Path, default=RAIZ_PROJETO / "custos.yaml")
    p.add_argument("--respostas", type=Path, default=RAIZ_PROJETO / "respostas.yaml")
    p.set_defaults(func=cmd_candidatar, fetch=None, copiloto_cfg=None)

    # --- LinkedIn ---
    p = sub.add_parser("login-linkedin", help="abre um Chromium VISÍVEL para VOCÊ fazer login no LinkedIn")
    p.add_argument("--perfil-navegador", type=Path, default=None, help=f"pasta do perfil (padrão {PASTA_PERFIL_PADRAO})")
    p.set_defaults(func=cmd_login_linkedin, copiloto_cfg=None)

    p = sub.add_parser("diagnosticar-linkedin", help="imprime só a ESTRUTURA da busca/vaga (sem textos) para ajustar seletores")
    p.add_argument("--linkedin", type=Path, default=RAIZ_PROJETO / "linkedin.yaml")
    p.add_argument("--perfil-navegador", type=Path, default=None)
    p.set_defaults(func=cmd_diagnosticar_linkedin, copiloto_cfg=None)

    p = sub.add_parser("aplicar-linkedin", help="AUTOMÁTICO: busca vagas, filtra e ENVIA a Candidatura Simplificada")
    _opcoes_comuns(p)
    p.add_argument("--max", type=int, default=None, help="no máximo N envios nesta execução (além do limite diário)")
    p.add_argument("--sem-enviar", action="store_true",
                   help="SIMULAÇÃO: percorre e preenche, mas NÃO envia (descarta o rascunho). Use no primeiro teste")
    p.add_argument("--linkedin", type=Path, default=RAIZ_PROJETO / "linkedin.yaml")
    p.add_argument("--pdf", type=Path, default=None, help="PDF do currículo (padrão: neutro em PT, gerado agora)")
    p.add_argument("--sem-log", action="store_true", help="não grava o log local de depuração")
    p.add_argument("--notificacao", type=Path, default=None)
    p.add_argument("--perfil-navegador", type=Path, default=None, help=f"pasta do perfil (padrão {PASTA_PERFIL_PADRAO})")
    p.add_argument("--saida", type=Path, default=SAIDA_PADRAO)
    p.add_argument("--perfil", type=Path, default=PERFIL_PADRAO)
    p.add_argument("--filtros", type=Path, default=RAIZ_PROJETO / "filtros.yaml")
    p.add_argument("--respostas", type=Path, default=RAIZ_PROJETO / "respostas.yaml")
    p.set_defaults(func=cmd_aplicar_linkedin, copiloto_cfg=None)

    p = sub.add_parser("parar", help="BOTÃO PARAR: cria o arquivo PARAR; tudo para e nada novo começa")
    p.add_argument("motivo", nargs="*", help="motivo (opcional, só para referência)")
    p.set_defaults(func=cmd_parar)

    p = sub.add_parser("retomar", help="apaga o arquivo PARAR")
    p.set_defaults(func=cmd_retomar)

    p = sub.add_parser("rodar", help="rodada diária (timer): buscar + filtrar; sem API paga, sem envio")
    _opcoes_comuns(p)
    p.add_argument("--fontes", type=Path, default=RAIZ_PROJETO / "fontes.yaml")
    p.add_argument("--filtros", type=Path, default=RAIZ_PROJETO / "filtros.yaml")
    p.add_argument("--notificacao", type=Path, default=None, help="config do ntfy (padrão notificacao.yaml)")
    p.set_defaults(func=cmd_rodar, http_factory=None)

    p = sub.add_parser("gerar-timer", help="gera (NÃO instala) bot-vagas.service/.timer do systemd --user")
    p.add_argument("--saida", type=Path, default=SAIDA_PADRAO / "systemd")
    p.add_argument("--hora", default="14:00", help="horário diário HH:MM (padrão 14:00)")
    p.add_argument("--python", type=Path, default=None, help="python a usar (padrão .venv/bin/python)")
    p.set_defaults(func=cmd_gerar_timer)

    p = sub.add_parser("testar-notificacao", help="manda UMA notificação de teste pelo ntfy (se configurado)")
    p.add_argument("--notificacao", type=Path, default=None)
    p.set_defaults(func=cmd_testar_notificacao)

    p = sub.add_parser("marcar", help="atualiza só o status de uma vaga: você diz quando enviou/desistiu")
    _opcoes_comuns(p)
    p.add_argument("alvo", metavar="CHAVE|URL")
    p.add_argument("status", choices=STATUS_MANUAIS)
    p.set_defaults(func=cmd_marcar)
    return parser


def main(argv: list[str] | None = None, *, client_factory=None, fetch=None, copiloto_cfg=None, http_factory=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    # compatibilidade com a Etapa 1: sem subcomando, gera o currículo
    if not argv or argv[0] not in SUBCOMANDOS and argv[0] not in ("-h", "--help"):
        argv = ["curriculo", *argv]
    parser = construir_parser()
    args = parser.parse_args(argv)
    comando = getattr(args, "comando", None)
    if comando in ("avaliar", "preparar") and client_factory is not None:
        args.client_factory = client_factory  # injeção para testes; nunca usada em produção
    if comando in ("preparar", "candidatar") and fetch is not None:
        args.fetch = fetch  # injeção para testes: nunca abre o Playwright
    if comando in ("candidatar", "login-gupy", "login-linkedin", "aplicar-linkedin", "diagnosticar-linkedin") and copiloto_cfg is not None:
        args.copiloto_cfg = copiloto_cfg  # injeção SÓ para testes (headless, loopback, perfil temporário)
    if comando == "rodar" and http_factory is not None:
        args.http_factory = http_factory  # injeção SÓ para testes: nunca acessa a rede
    from .parada import CODIGO_SAIDA_PARADO, ParadaSolicitada, parar_solicitado

    if comando in COMANDOS_BLOQUEADOS_POR_PARAR and parar_solicitado():
        print(str(ParadaSolicitada()), file=sys.stderr)
        return CODIGO_SAIDA_PARADO
    try:
        return args.func(args)
    except ParadaSolicitada as e:
        print(f"\n{e}", file=sys.stderr)
        return CODIGO_SAIDA_PARADO
    except KeyboardInterrupt:
        print("\ninterrompido (Ctrl+C). O que já foi gravado no banco continua lá.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
