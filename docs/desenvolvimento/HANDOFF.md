# HANDOFF — bot de candidaturas do Thiago (2026-11-16)

Leia primeiro: `docs/DECISOES.md` (decisões atuais) e este arquivo. Documento de trabalho para agentes de código. Responda ao Thiago em português.
Ele é estudante do 2º semestre de CS: ao entregar trabalho, diga o que ele deve conferir pessoalmente.

## Estado (atualizado em 2026-11-18)
- Python 3.12, venv em `.venv/`. Testes: `.venv/bin/pytest -q` -> 280 passam (verificado em 2026-11-18, com e sem
  `perfil.yaml`, num clone limpo). Obs.: o Playwright do pip precisa do Chromium da MESMA versão
  (`.venv/bin/playwright install chromium`).
- Pronto: perfil + gerador de currículo PT/EN (1 página) + verificador de fatos; filtro (só estágio/trainee,
  remoto OU Maceió/AL); fontes Greenhouse/Lever/Gupy (busca pública do portal); avaliador e plano com Haiku 5.5
  (travados atrás de `--real`); contador de gasto (teto US$ 3/mês); painel local; `preparar <url>`; copiloto
  semi-automático da Gupy (`login-gupy`, `candidatar`, `marcar`) — NUNCA clica em nada, Thiago clica e envia.
- Novo em 2026-11-18:
  - Copiloto encerra limpo ao fechar a janela, Ctrl+C, SIGTERM/SIGHUP ou PARAR: sempre imprime resumo e grava
    `detalhes.assistencia` (com `encerramento`). Causa do travamento antigo: `time.sleep` não processa eventos do
    Playwright (fechamento nunca percebido) e um KeyboardInterrupt dentro do Playwright quebrava o despachante
    (ctx.close() girava para sempre). Agora: espera com `ctx.wait_for_event("close")`, sinais só ligam uma flag,
    fechamento com prazo (setitimer). Testado com sinal real no grupo de processos e Chromium visível sob Xvfb.
  - Perfil do navegador em uso -> mensagem curta em português, código 3, status da vaga restaurado.
  - Log de depuração `~/.local/share/bot-vagas/logs/candidatar-*.jsonl` (0700/0600), sem valores; `--sem-log`.
  - Aviso "PRECISA DE LOGIN" uma vez por mudança de página, por aba.
  - Bug corrigido: `Copiloto._pular` era chamado mas não existia (cada campo pulado virava AttributeError e aviso
    repetido a cada varredura).
  - Botão PARAR (`parada.py`): `bot-vagas parar|retomar`, botão no painel (POST com checagem de Origin), checado
    entre fontes/vagas e a cada ciclo do copiloto; comandos de trabalho não começam com PARAR (código 4).
  - ntfy.sh opcional (`notificacao.py`, `notificacao.yaml` fora do git): só mensagens fixas; desligado por padrão.
  - `bot-vagas rodar` (buscar + filtrar, sem API paga) e `bot-vagas gerar-timer` (gera, não instala, o timer 14h).
  - README.md de uso.
- Uso real até agora: 1 candidatura enviada com o fluxo manual, marcada `enviada`.
- Nenhuma chamada paga à API feita ainda.

## Próximos passos sugeridos (depois dos itens [Thiago])
1. Calibrar `PADROES_CAMPOS` do copiloto com o log de depuração da próxima candidatura real.
2. Calibrar a nota de corte (docs/DECISOES.md, "Em aberto") depois de ~30 vagas avaliadas, quando o Thiago autorizar `--real`.
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
- [x] Copiloto: fechar a janela do Chromium ou Ctrl+C encerra limpo, imprime resumo e grava `detalhes.assistencia` (pendência 1a), com testes
- [x] Copiloto: perfil do navegador em uso -> mensagem clara em português, sem traceback, saída != 0 (1b), com testes
- [x] Copiloto: log local de depuração 0600 sem valores pessoais, `--sem-log` (1c), com teste de não vazamento
- [x] Copiloto: aviso "PRECISA DE LOGIN" uma vez por mudança de página (1d)
- [x] Botão PARAR (arquivo `PARAR` e/ou painel) interrompe qualquer execução, com testes
- [x] Notificação opcional ntfy.sh (texto genérico, sem dados pessoais), desligada por padrão, com testes
- [x] Arquivos de `systemd --user` timer às 14h (`Persistent=true`) gerados e documentados (NÃO instalar)
- [x] README de uso: instalação num clone limpo (usar `perfil.example.yaml`/`respostas.example.yaml`), comandos, primeira execução, o que conferir
- [x] Verificação final: `.venv/bin/pytest -q` todos passando COM e SEM `perfil.yaml`; grep confirmando que nada lê chave da API, não há `.click()`/submit no copiloto e nenhum dado pessoal em código/logs/fixtures
- [x] `HANDOFF.md` e `REQUISITOS.md` atualizados com o estado final
- [ ] [Thiago] Instalar o timer no PC dele
- [ ] [Thiago] Fazer a próxima candidatura real com o copiloto e mostrar o log de depuração
- [ ] [Thiago] Definir o teto de gasto no painel da Anthropic antes de qualquer `--real`

## Regra de continuidade
NÃO pare para "dar um relatório parcial" nem pergunte se deve continuar: faça os itens em sequência até o fim. Só pare antes disso
se houver um bloqueio REAL que só o Thiago resolve (nesse caso crie `.claude/BLOQUEADO.md` explicando o bloqueio e o que falta).
Se o limite de uso acabar, nada se perde: o estado está neste arquivo; quem retomar continua do primeiro item `[ ]`.
Há um hook de Stop (`.claude/hooks/nao-parar.sh`) que bloqueia o encerramento enquanto restarem itens, quando existir
`.claude/NAO_PARAR`. Ao terminar tudo, ele se desativa sozinho.
