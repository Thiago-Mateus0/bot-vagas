# HANDOFF — bot de candidaturas do Thiago (2026-11-16)

Leia primeiro: `REQUISITOS.md` (decisões) e este arquivo. Responda ao Thiago em português.
Ele é estudante do 2º semestre de CS: ao entregar trabalho, diga o que ele deve conferir pessoalmente.

## Estado
- Python 3.12, venv em `.venv/`. Testes: `.venv/bin/pytest -q` -> 238 passam (verificado em 2026-11-16).
- Pronto: perfil + gerador de currículo PT/EN (1 página) + verificador de fatos; filtro (só estágio/trainee,
  remoto OU Maceió/AL); fontes Greenhouse/Lever/Gupy (busca pública do portal); avaliador e plano com Haiku 5.5
  (travados atrás de `--real`); contador de gasto (teto US$ 3/mês); painel local somente leitura;
  `preparar <url>` (pacote de candidatura, não envia); copiloto semi-automático da Gupy
  (`login-gupy`, `candidatar`, `marcar`) — NUNCA clica em nada, Thiago clica e envia.
- Uso real até agora: 1 candidatura enviada (CCEE, estágio Governança de TI), marcada `enviada`.
- Nenhuma chamada paga à API feita ainda.

## Pendências (em ordem)
1. **Corrigir o copiloto** (src/bot_vagas/copiloto.py, cli.py). Um agente foi cortado por limite de uso no meio disso.
   Estado do código: provavelmente intacto (238 testes passam), mas a correção NÃO foi concluída. Problemas reais vistos
   na primeira execução com a Gupy de verdade:
   a) Fechar a janela do Chromium NÃO encerra `candidatar`: fica preso em `time.sleep` e nunca imprime o resumo nem grava
      `detalhes.assistencia`. Ctrl+C gera traceback e também não grava. Corrigir: detectar fechamento do contexto/páginas
      (evento close, TargetClosedError), tratar SIGINT/SIGTERM, e SEMPRE imprimir resumo + gravar (try/finally).
      Não marcar `enviada` automaticamente (isso é do `marcar`).
   b) Perfil do navegador já em uso (Chromium aberto de outra execução) -> hoje traceback enorme; mostrar UMA mensagem
      clara em português e sair com código != 0 (vale para `login-gupy` e `candidatar`).
   c) Log local de depuração (~/.local/share/bot-vagas/logs/candidatar-<data>.jsonl, 0600): por campo visto, rótulo/tipo,
      categoria atribuída, preenchido/pendente/pulou e motivo. NUNCA valores, senhas, e-mails, cookies ou texto da vaga.
      `--sem-log` desliga. Testar que valores fictícios do respostas.yaml não aparecem no arquivo.
   d) Aviso "PRECISA DE LOGIN" deve aparecer uma vez por mudança de página, não a cada polling.
2. Botão PARAR (arquivo `PARAR` e/ou botão no painel) que interrompe qualquer execução.
3. Notificação opcional via ntfy.sh quando o copiloto/bot pausar (só texto genérico, sem dados pessoais).
4. Agendamento: gerar (NÃO instalar) arquivos de `systemd --user` timer às 14h com `Persistent=true` e documentar como
   instalar. Instalar no PC do Thiago é com ele.
5. README curto de uso (comandos, primeira execução, o que conferir).
- FORA de escopo por decisão do Thiago/risco: LinkedIn e Indeed; envio automático sem clique; resolver captcha.

## Regras que valem sempre
- Sem chamadas reais à API da Anthropic (nem nos testes). `--real` só com autorização explícita do Thiago e teto definido
  na conta dele. O código NÃO lê ANTHROPIC_API_KEY (o SDK lê sozinho).
- Dados pessoais só em `perfil.yaml` e `respostas.yaml` (locais). Nunca enviar ao modelo, nunca em logs, nunca em repositório.
- Nunca editar/apagar `vagas.db` à mão; testes usam banco temporário. Nunca usar o perfil do navegador real
  (`~/.local/share/bot-vagas/navegador`) nem acessar a Gupy real em testes.
- Sem evasão de detecção de automação (nada de stealth, webdriver, UA falso). Se a Gupy bloquear: parar e avisar.
- Sem commit/push sem mostrar o diff ao Thiago e ele aprovar. Nunca pôr co-autoria/"Generated with" em commits.
- Verifique SEMPRE os testes por conta própria antes de dizer que algo está pronto; se algo falhar, diga.

## Mercado (para calibrar expectativas)
Com os filtros atuais (estágio/trainee, remoto ou Maceió) sobram 1 a 3 vagas de TI por semana. As APIs Greenhouse/Lever
quase não têm vaga elegível; a Gupy é a fonte principal. A Gupy desligou a API pública antiga em 2026-11-09; o bot lê a
página pública de busca (rota não documentada, pode mudar: o bot avisa e para, não contorna).

## Definição de pronto (o agente só encerra quando TODOS os itens abaixo estiverem `[x]`)
Marque `[x]` neste arquivo ao concluir cada item (com teste e verificação feitos por você). Itens `[Thiago]` não contam:
dependem dele (PC/conta dele) e ficam documentados no README.
- [ ] Copiloto: fechar a janela do Chromium ou Ctrl+C encerra limpo, imprime resumo e grava `detalhes.assistencia` (pendência 1a), com testes
- [ ] Copiloto: perfil do navegador em uso -> mensagem clara em português, sem traceback, saída != 0 (1b), com testes
- [ ] Copiloto: log local de depuração 0600 sem valores pessoais, `--sem-log` (1c), com teste de não vazamento
- [ ] Copiloto: aviso "PRECISA DE LOGIN" uma vez por mudança de página (1d)
- [ ] Botão PARAR (arquivo `PARAR` e/ou painel) interrompe qualquer execução, com testes
- [ ] Notificação opcional ntfy.sh (texto genérico, sem dados pessoais), desligada por padrão, com testes
- [ ] Arquivos de `systemd --user` timer às 14h (`Persistent=true`) gerados e documentados (NÃO instalar)
- [x] README de uso: instalação num clone limpo (usar `perfil.example.yaml`/`respostas.example.yaml`), comandos, primeira execução, o que conferir
- [ ] Verificação final: `.venv/bin/pytest -q` todos passando COM e SEM `perfil.yaml`; grep confirmando que nada lê chave da API, não há `.click()`/submit no copiloto e nenhum dado pessoal em código/logs/fixtures
- [ ] `HANDOFF.md` e `REQUISITOS.md` atualizados com o estado final
- [ ] [Thiago] Instalar o timer no PC dele
- [ ] [Thiago] Fazer a próxima candidatura real com o copiloto e mostrar o log de depuração
- [ ] [Thiago] Definir o teto de gasto no painel da Anthropic antes de qualquer `--real`

## Regra de continuidade
NÃO pare para "dar um relatório parcial" nem pergunte se deve continuar: faça os itens em sequência até o fim. Só pare antes disso
se houver um bloqueio REAL que só o Thiago resolve (nesse caso crie `.claude/BLOQUEADO.md` explicando o bloqueio e o que falta).
Se o limite de uso acabar, nada se perde: o estado está neste arquivo; quem retomar continua do primeiro item `[ ]`.
Há um hook de Stop (`.claude/hooks/nao-parar.sh`) que bloqueia o encerramento enquanto restarem itens, quando existir
`.claude/NAO_PARAR`. Ao terminar tudo, ele se desativa sozinho.
