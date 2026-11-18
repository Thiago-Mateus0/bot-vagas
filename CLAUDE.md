# bot-vagas — instruções para o agente

Leia primeiro `README.md` e `docs/desenvolvimento/HANDOFF.md` (estado e pendências). Responda ao Thiago em português.
Ele está no 2º semestre de CS: ao entregar trabalho, diga o que ele deve conferir pessoalmente.

## Regras que valem sempre (os hooks em `.claude/hooks/` bloqueiam parte delas)
- Sem chamadas reais à API da Anthropic, nem nos testes. `--real` só com autorização explícita do Thiago.
  O código NÃO lê `ANTHROPIC_API_KEY`.
- Dados pessoais só em `perfil.yaml` e `respostas.yaml` (locais). Não leia esses arquivos; nunca envie ao modelo,
  nunca em logs, fixtures ou repositório. Use `perfil.example.yaml` / `respostas.example.yaml`.
- Nunca edite/apague `vagas.db` à mão; testes usam banco temporário. Nunca use o perfil real do navegador
  (`~/.local/share/bot-vagas/navegador`) nem acesse a Gupy real em testes.
- Copiloto da Gupy NUNCA clica nem envia (`.click()`, submit): quem clica é o Thiago.
- Sem evasão de detecção de automação (stealth, webdriver, UA falso). Se a Gupy bloquear: pare e avise.
- LinkedIn, Indeed, envio automático e captcha estão fora de escopo.
- Sem commit/push sem mostrar o diff ao Thiago e ele aprovar. Sem co-autoria/"Generated with" nos commits.
- Testes: `.venv/bin/pytest -q`. Rode por conta própria antes de dizer que algo está pronto; se falhar, diga.

## Fluxo
- Trabalhe os itens `[ ]` da "Definição de pronto" do `docs/desenvolvimento/HANDOFF.md` em sequência, sem relatórios parciais.
  Bloqueio real que só o Thiago resolve: crie `.claude/BLOQUEADO.md` e pare.
- Antes de marcar o item de "Verificação final", rode o subagente `revisor` e corrija o que ele apontar.
