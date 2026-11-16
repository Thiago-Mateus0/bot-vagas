#!/usr/bin/env bash
# Hook de Stop: impede o agente de encerrar enquanto houver itens "- [ ]" na seção
# "Definição de pronto" do HANDOFF.md (itens marcados [Thiago] não contam).
# Só atua se existir .claude/NAO_PARAR. Escapes seguros: .claude/BLOQUEADO.md (bloqueio real) e
# limite de 40 bloqueios consecutivos. Ao concluir tudo, remove o marcador e libera.
set -u
cd "$(dirname "$0")/../.." || exit 0
[ -f .claude/NAO_PARAR ] || exit 0
[ -f .claude/BLOQUEADO.md ] && exit 0
n=$(cat .claude/.bloqueios 2>/dev/null || echo 0)
case "$n" in ''|*[!0-9]*) n=0 ;; esac
[ "$n" -ge 40 ] && exit 0
pend=$(awk '/^## Definição de pronto/{f=1;next} /^## /{f=0} f && /^- \[ \]/ && !/\[Thiago\]/' HANDOFF.md)
if [ -z "$pend" ]; then rm -f .claude/NAO_PARAR .claude/.bloqueios; exit 0; fi
echo $((n+1)) > .claude/.bloqueios
PEND="$pend" python3 -I - <<'PY'
import json, os
motivo = ("Ainda restam itens na Definição de pronto do HANDOFF.md. Continue trabalhando, sem relatório parcial, "
          "até concluí-los (ou crie .claude/BLOQUEADO.md se houver um bloqueio real). Restam:\n" + os.environ["PEND"])
print(json.dumps({"decision": "block", "reason": motivo}, ensure_ascii=False))
PY
exit 0
