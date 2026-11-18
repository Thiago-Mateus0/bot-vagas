#!/usr/bin/env bash
# Hook PostToolUse: depois de editar código ou testes, roda a suíte e devolve falhas ao agente.
set -u
cd "$(dirname "$0")/../.." || exit 0
arq=$(python3 -I -c 'import json,sys; d=json.load(sys.stdin); print((d.get("tool_input") or {}).get("file_path",""))')
case "$arq" in
  */src/*.py|*/tests/*.py|*/src/*.html|*/src/*.css) ;;
  *) exit 0 ;;
esac
if [ -x .venv/bin/pytest ]; then py=.venv/bin/pytest; else py="python3 -m pytest"; fi
saida=$($py -q -x --no-header 2>&1); rc=$?
saida=$(printf '%s\n' "$saida" | tail -n 25)
if [ "$rc" -ne 0 ]; then
  printf 'Testes falhando depois de editar %s:\n%s\n' "$arq" "$saida" >&2
  exit 2
fi
exit 0
