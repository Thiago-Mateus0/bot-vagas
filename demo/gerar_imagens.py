"""Gera as imagens do README em docs/img/ com dados 100% FICTÍCIOS (sem rede, sem API, sem a Gupy real).

    .venv/bin/python demo/gerar_imagens.py

- painel.png / detalhe.png: painel local com o banco de demonstração (`criar_demo.py --vitrine`);
- copiloto.gif: o copiloto preenchendo demo/formulario-exemplo.html (portal fictício servido em 127.0.0.1).
Precisa do ffmpeg para o GIF.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import threading
import time
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "src"))
sys.path.insert(0, str(RAIZ / "demo"))

from criar_demo import criar  # noqa: E402

from bot_vagas.copiloto import Alvos, Copiloto  # noqa: E402
from bot_vagas.gasto import CUSTOS_PADRAO  # noqa: E402
from bot_vagas.painel import criar_app  # noqa: E402

DESTINO = RAIZ / "docs" / "img"

# respostas FICTÍCIAS, só para a demonstração (não são de ninguém)
RESPOSTAS_DEMO = {
    "disponibilidade": {"inicio": "imediata"},
    "tipo_contrato_aceito": ["estagio"],
    "localizacao": {"cidade": "São Paulo", "estado": "SP", "pais": "Brasil", "aceita_remoto": True},
    "idiomas": {"ingles": "avançado"},
    "extras": {"como_conheceu_a_vaga": "Portal de vagas"},
}


class _Quieto(SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


def _servir_arquivos(pasta: Path) -> tuple[ThreadingHTTPServer, str]:
    srv = ThreadingHTTPServer(("127.0.0.1", 0), partial(_Quieto, directory=str(pasta)))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


def _servir_painel(banco: Path):
    import uvicorn

    config = uvicorn.Config(criar_app(banco, CUSTOS_PADRAO), host="127.0.0.1", port=0, log_level="warning")
    servidor = uvicorn.Server(config)
    threading.Thread(target=servidor.run, daemon=True).start()
    while not servidor.started:
        time.sleep(0.05)
    porta = servidor.servers[0].sockets[0].getsockname()[1]
    return servidor, f"http://127.0.0.1:{porta}"


def imagens_do_painel(navegador, tmp: Path) -> None:
    banco = criar(tmp / "demo.db", gasto_percentual=35, vitrine=True)
    servidor, base = _servir_painel(banco)
    try:
        ctx = navegador.new_context(viewport={"width": 1280, "height": 760}, device_scale_factor=2, locale="pt-BR",
                                    color_scheme="light")
        pg = ctx.new_page()
        pg.goto(base + "/")
        pg.screenshot(path=str(DESTINO / "painel.png"))
        pg.get_by_role("link", name="Estágio Desenvolvimento Backend").first.click()  # navegação do painel local
        pg.wait_for_load_state()
        pg.screenshot(path=str(DESTINO / "detalhe.png"), full_page=True)
        ctx.close()
    finally:
        servidor.should_exit = True


def gif_do_copiloto(navegador, tmp: Path) -> None:
    pdf = tmp / "curriculo-exemplo.pdf"
    pdf.write_bytes(b"%PDF-1.4\n%demo\n%%EOF\n")
    srv, base = _servir_arquivos(RAIZ / "demo")
    quadros = []
    try:
        ctx = navegador.new_context(viewport={"width": 1000, "height": 780}, device_scale_factor=1, locale="pt-BR")
        pg = ctx.new_page()
        pg.goto(base + "/formulario-exemplo.html")
        copiloto = Copiloto(RESPOSTAS_DEMO, pdf, alvos=Alvos(permitir_loopback=True), log=lambda *a, **k: None)

        def quadro(nome: str, segundos: float) -> None:
            caminho = tmp / f"{len(quadros):02d}-{nome}.png"
            pg.screenshot(path=str(caminho))
            quadros.append((caminho, segundos))

        quadro("vazio", 1.8)
        copiloto.passo(pg)
        quadro("preenchido", 3.5)
        pg.mouse.wheel(0, 600)  # rolagem da página (não é clique)
        time.sleep(0.3)
        quadro("pendentes", 4.0)
        ctx.close()
    finally:
        srv.shutdown()

    lista = tmp / "quadros.txt"
    linhas = []
    for caminho, seg in quadros:
        linhas += [f"file '{caminho}'", f"duration {seg}"]
    linhas.append(f"file '{quadros[-1][0]}'")  # o último quadro precisa repetir no concat
    lista.write_text("\n".join(linhas) + "\n", encoding="utf-8")
    filtro = "fps=10,scale=900:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=128[p];[b][p]paletteuse=dither=bayer"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lista),
                    "-vf", filtro, "-loop", "0", str(DESTINO / "copiloto.gif")], check=True)


def main() -> int:
    from playwright.sync_api import sync_playwright

    if not shutil.which("ffmpeg"):
        print("precisa do ffmpeg para gerar o GIF", file=sys.stderr)
        return 1
    DESTINO.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as t, sync_playwright() as p:
        navegador = p.chromium.launch()
        imagens_do_painel(navegador, Path(t))
        gif_do_copiloto(navegador, Path(t))
        navegador.close()
    for nome in ("painel.png", "detalhe.png", "copiloto.gif"):
        print(f"gerado: {DESTINO / nome} ({(DESTINO / nome).stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
