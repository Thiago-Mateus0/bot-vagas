#!/usr/bin/env bash
# Hook PreToolUse: bloqueia ações proibidas pelo CLAUDE.md antes de acontecerem.
# É uma grade de proteção, não um sandbox: a regra continua valendo mesmo onde o hook não alcança.
# Saída 2 = bloqueia a ferramenta e devolve o motivo (stderr) ao agente.
python3 -I -c '
import json, re, sys
d = json.load(sys.stdin)
ferr = d.get("tool_name", "")
ent = d.get("tool_input") or {}
PESSOAIS = r"(?<![\w.-])(perfil|respostas)\.yaml\b|vagas\.db\b|bot-vagas/navegador"
def bloquear(msg):
    print("BLOQUEADO pelo hook proteger.sh: " + msg, file=sys.stderr); sys.exit(2)
if ferr == "Bash":
    cmd = ent.get("command", "")
    if re.search(r"(^|\s)--real(\s|$|=)", cmd):
        bloquear("--real faz chamadas pagas à API; só com autorização explícita do Thiago (ele roda).")
    # Exceção aprovada pelo Thiago (2026-11-18): a tarefa agendada pode commitar/pushar SÓ na branch agente/handoff.
    import subprocess
    try:
        ramo = subprocess.run(["git", "branch", "--show-current"], capture_output=True, text=True, timeout=5).stdout.strip()
    except Exception:
        ramo = ""
    liberado = ramo == "agente/handoff" and not re.search(r"\b(main|master)\b|--force|-f\b|--delete|:", cmd.split("push", 1)[-1] if "push" in cmd else "")
    if re.search(r"\bgit\b[^|;&]*\b(commit|push)\b", cmd) and not liberado:
        bloquear("commit/push só depois de mostrar o diff ao Thiago e ele aprovar (ele mesmo faz).")
    if re.search(PESSOAIS, cmd) and re.search(
        r"\b(cat|head|tail|less|more|grep|rg|sed|awk|cp|scp|base64|xxd|od|strings|sqlite3|python3?|curl|rm|tee|vim?|nano)\b|>", cmd):
        bloquear("comando lê/altera arquivo com dados pessoais (perfil.yaml, respostas.yaml, vagas.db ou perfil do navegador). Use os .example.yaml ou um banco temporário.")
elif ferr in ("Read", "Edit", "Write", "MultiEdit", "NotebookEdit"):
    p = ent.get("file_path") or ent.get("notebook_path") or ""
    if re.search(PESSOAIS, p):
        bloquear(p + " contém dados pessoais e não pode ser lido/alterado pelo agente.")
elif ferr in ("Grep", "Glob"):
    alvo = (ent.get("path") or "") + " " + (ent.get("glob") or "") + " " + (ent.get("pattern") or "")
    if re.search(PESSOAIS, alvo):
        bloquear("busca mira arquivo com dados pessoais.")
sys.exit(0)
'
