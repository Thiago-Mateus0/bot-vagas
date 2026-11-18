# HANDOFF — bot de candidaturas do Thiago (2026-11-16)

Leia primeiro: `REQUISITOS.md` (decisões) e este arquivo. Responda ao Thiago em português.
Ele é estudante do 2º semestre de CS: ao entregar trabalho, diga o que ele deve conferir pessoalmente.

## Estado (fim de 2026-11-16)
- Python 3.12, venv em `.venv/`. Testes: `.venv/bin/pytest -q` -> 321 passam (eram 293 antes do modo automático) COM `perfil.yaml` e também num clone
  limpo SEM `perfil.yaml` (292 + 1 pulado: o teste que compara `systemd/` só vale com o clone em ~/bot-vagas).
- Pronto: perfil + gerador de currículo PT/EN (1 página) + verificador de fatos; filtro (só estágio/trainee,
  remoto OU Maceió/AL); fontes Greenhouse/Lever/Gupy (busca pública do portal); avaliador e plano com Haiku 5.5
  (travados atrás de `--real`); contador de gasto (teto US$ 3/mês); painel local (somente leitura + botão PARAR);
  `preparar <url>` (pacote de candidatura, não envia); copiloto semi-automático da Gupy
  (`login-gupy`, `candidatar`, `marcar`) — NUNCA clica em nada, Thiago clica e envia.
- Feito nesta sessão (mudanças NÃO commitadas, aguardando o Thiago revisar o diff):
  - Copiloto: fechar aba/janela, Ctrl+C e SIGTERM encerram limpo, SEMPRE imprimem o resumo e gravam
    `detalhes.assistencia` (com `encerramento`). Causa do travamento: `time.sleep` no modo síncrono do Playwright
    não processa eventos; agora a espera é `page.wait_for_timeout` + evento `close` + tratador de sinais.
  - Bug achado e corrigido: `Copiloto._pular` era chamado mas não existia; todo campo "pulado" virava
    "falha ao tratar campo" em silêncio.
  - Perfil do navegador em uso: checa o `SingletonLock` antes de abrir e traduz o erro `ProcessSingleton` do
    Chromium; uma linha em português, código 3, sem traceback (login-gupy e candidatar). Testado com Chromium real.
  - Log local de depuração `~/.local/share/bot-vagas/logs/candidatar-AAAAMMDD-HHMMSS.jsonl` (pasta 0700, arquivo
    0600), `--sem-log` desliga; redige valores de respostas.yaml, contato do perfil, e-mails, números longos e query.
  - "PRECISA DE LOGIN" uma vez por mudança de página, por aba (antes, duas abas faziam o aviso repetir).
  - Botão PARAR: `parada.py`, `bot-vagas parar|retomar`, botões no painel (POST só com Origin/Referer local);
    checado entre fontes, páginas da Gupy, vagas, antes de cada chamada paga e a cada ciclo do copiloto.
  - ntfy.sh opcional (`notificacao.py`, `notificacao.example.yaml`, desligado por padrão, textos fixos):
    eventos login (copiloto), alarme_fonte (buscar/rodar), teto (avaliar); `bot-vagas testar-notificacao`.
  - `bot-vagas rodar` (buscar + filtrar, sem API, sem envio) e `bot-vagas gerar-timer` -> `systemd/bot-vagas.{service,timer}`
    (14h, Persistent=true, `%h/bot-vagas`), validados com `systemd-analyze verify`; NÃO instalados.
  - README.md de uso. Testes isolados: PARAR, notificacao.yaml e logs vão para pastas temporárias (conftest).
  - Valores de teste que coincidiam com respostas reais do Thiago (gênero, raça/cor, formatura, pretensão)
    trocados por fictícios.
- Códigos de saída: 0 ok; 1 erro/alarme; 2 recusado; 3 perfil do navegador em uso; 4 PARAR; 130 Ctrl+C/SIGTERM.
- Uso real até agora: 1 candidatura enviada (CCEE, estágio Governança de TI), marcada `enviada`.
- Nenhuma chamada paga à API feita ainda.
- Não testado de verdade (só com fixtures/headless): fechar a janela VISÍVEL do Chromium na Gupy real. É a próxima
  candidatura do Thiago que confirma (ver itens [Thiago]).

## Modo 100% automático (decisão do Thiago em 2026-11-16, risco de banimento aceito)
Respostas dele: (1) o bot PODE marcar termos/LGPD; (2) perguntas abertas/testes: gerar resposta com o Haiku;
(3) sites: Gupy + LinkedIn + Indeed; (4) começar com 1 semana em SIMULAÇÃO.
- Feito (Gupy): `piloto.py` (única parte que clica), `automacao.py` (automacao.yaml: modo simulacao|envio,
  limites diário/por site, janela 14–17h, pausas aleatórias, elegíveis), `respostas_abertas.py` (Haiku +
  verificador de fatos; só com `usar_modelo_para_perguntas_abertas: true`), `bot-vagas automatico`, `rodar` chama
  o automático se `ativo: true`. Status novos: `simulada`, `pulada`. Screenshots em saida/capturas/ (§15.6).
  Testes: tests/test_piloto.py contra fixtures de várias telas em tests/fixtures/auto/.
- Bug corrigido no caminho: a classificação de campo usava o texto em volta com o mesmo peso do rótulo
  ("Cidade" virava pretensão; "e-mail" numa newsletter fazia a pergunta aberta virar "login"). Agora
  `classificar_campo` usa rótulo/aria/placeholder primeiro e o texto em volta só se nada casar.
- NÃO validado contra a Gupy real (os testes nunca acessam a Gupy): os textos de botão (RE_FINAL/RE_AVANCAR/
  RE_INICIAR em piloto.py) e o reconhecimento de campos são calibrados na semana de simulação do Thiago, com os
  screenshots e o log `automatico-*.jsonl` (inclui os textos de botões vistos por tela).
- Visto na Gupy real (2026-11-16): o 'Candidatar-se' leva a `<empresa>.gupy.io/candidates/signin`, que chega
  EM BRANCO (SPA) e pede só o e-mail. O piloto espera a página renderizar (ESPERA_RENDER_MS), reconhece login
  pelo endereço (RE_URL_LOGIN) e espera ESPERA_SSO_MS por redirecionamento automático. Página que não carrega
  = `nao_carregou` (status volta, tenta depois). EM ABERTO: se a sessão vale para todas as empresas ou se
  cada subdomínio pede login (aí o 100% automático exigiria o bot digitar credenciais: decisão do Thiago).
- Visto na Starian: o 'Candidatar-se' levou ao OAuth do LinkedIn (www.linkedin.com/uas/login). O piloto espera
  ESPERA_SSO_MS voltar sozinho para a Gupy; se ficar no login do provedor (PROVEDORES_LOGIN), para a rodada por
  login (status volta). `bot-vagas marcar <link> fila` devolve à fila uma vaga pulada por engano.
- Com o LinkedIn logado no perfil do bot, o OAuth volta sozinho (confirmado na Starian) e cai em
  `/candidates/auth/linkedin` ("Validando suas informações"): o piloto espera telas de transição
  (RE_CARREGANDO/RE_URL_TRANSICAO, até ESPERA_TRANSICAO_MS) e olha de novo uma vez antes de dizer "sem botão".

- 2026-11-17: Starian e v(dev) chegaram ao formulário (`/candidates/jobs/<id>/apply`) logadas via LinkedIn, sem
  digitar nada. Lá a Gupy abre uma aba about:blank: `_pagina_ativa` ignora abas não-http e `_aba_fechada`
  faz o piloto olhar a tela de novo (até 3x) se a aba fechar no meio (antes: "fora da Gupy (?)" e
  TargetClosedError). Screenshots agora 0600 (mostram e-mail/dados do formulário).
- 2026-11-17: com candidatura iniciada, a vaga abre direto no /apply, com cabeçalho/rodapé prontos e o formulário
  num spinner (MUI CircularProgress). `_JS_SPINNER` conta spinner visível sem nenhum campo como "carregando"
  (barras de etapa com aria-valuenow não contam).
- 2026-11-16 a sessão do LinkedIn no perfil do bot CAIU entre 17:27 e 18:18 ("Olá novamente", com e-mail e senha
  preenchidos pelo gerenciador de senhas do Chromium). O bot não clicou: parou por login.

- 2026-11-17: a GUPY também pede login de novo (starian.gupy.io/candidates/signin: e-mail/CPF + senha, ou
  Google/LinkedIn) com a sessão do LinkedIn ainda válida. Opção `entrar_com_linkedin` (automacao.yaml, padrão
  false): nessa tela o bot clica SÓ no botão "LinkedIn" (uma vez por vaga, nenhum campo tocado); o LinkedIn
  autentica com a sessão salva. Se o LinkedIn pedir senha, vale a regra B abaixo.
- v(dev): `TargetClosedError` duas vezes perto do /apply; causa NÃO confirmada (não reproduzi aba fechando sozinha;
  suspeita: janela do bot fechada durante a pausa). Agora: fechar a janela = rodada para com "janela fechada" e a
  vaga não é culpada; abertura da vaga protegida contra aba fechada; erro do navegador grava a mensagem (sem query).

- 2026-11-17 11:58: com a sessão VÁLIDA, linkedin.com/uas/login ficou em branco > 6 s antes de devolver para a
  Gupy. `_esperar_voltar_para_gupy` agora espera até ESPERA_TRANSICAO_MS em página de provedor sem formulário e
  desiste após ESPERA_SSO_MS só se aparecer formulário de login (`_JS_FORM_VISIVEL`).

- 2026-11-17 14:04: com `entrar_com_linkedin`, o bot clicou em 'LinkedIn', o LinkedIn passou sozinho, a Gupy ficou
  ~24 s em /candidates/auth/linkedin ("Validando") e VOLTOU para /candidates/signin. A Gupy recusou o login
  social. Causa em aberto: conta Gupy sem o LinkedIn vinculado (e-mails diferentes?) ou recusa ao navegador
  automatizado (não contornável). Diagnóstico pedido ao Thiago: repetir à mão no `login-gupy`.
- v(dev) 12:01: "navegador fechado ou parada pedida durante a vaga" = o Chromium inteiro fechou ao abrir a vaga.
  Se o Thiago não fechou a janela, o navegador está caindo nessa página (investigar).

- 2026-11-17 (teste à mão do Thiago): com o botão LinkedIn ele CHEGOU ao formulário e a janela FECHOU SOZINHA.
  Preferences do perfil: exit_type Normal, crashed:false => a Gupy fecha a aba (window.close), e sendo a última
  o Chromium sai (era o "navegador fechado" da v(dev)). Correção: aba ÂNCORA (about:blank) aberta a rodada toda;
  sem aba da vaga viva => reabre a vaga (máx. 2x); voltou ao login depois do LinkedIn => reabre a vaga 1x antes
  de concluir "a Gupy não aceitou". Não confirmado na Gupy real ainda.
- Fonte nova `velrino` (fontes/velrino.py): GitHub Issues de velrino/jobs, etiquetas level:intern + region:brazil.
  Links /jobs/<id> da Gupy viram o endereço do portal (job/<base64>); Banco.salvar_vagas não duplica URL
  normalizada igual (vaga em duas fontes). Validado na API real em 17/11: 32 vagas, 23 da Gupy, 10 remotas.
  Risco: repositório de um mantenedor, sem licença declarada (uso pessoal; não republicar).

## Decisão do Thiago (2026-11-17): o bot DIGITA o login da Gupy (chaveiro do sistema)
- LinkedIn abandonado (sessão no perfil do bot caía em ~1 h). Senha salva no Chromium descartada: o Chromium do
  bot não ofereceu salvar, e só preencheria sozinho no subdomínio exato de cada empresa.
- A sessão da Gupy (login próprio) vale para todas as empresas e dura ~1 dia (visto 16-17/11).
- `credenciais.py` (keyring/Secret Service, serviço `bot-vagas-gupy`) + `bot-vagas senha-gupy`. Opção
  `digitar_senha_gupy`: `_login_digitando` só em hosts da allowlist (gupy.io), marca o campo de e-mail/CPF
  (nunca campo de código) e o de senha, `fill`, espera o botão liberar e clica; fluxo e-mail→senha suportado.
  Senha digitada no máximo 1x por vaga; login pedido de novo => para a rodada (rodada termina; sem retentativa).
  Nunca em log/banco/mensagem (teste dedicado). Testado só com fixtures; NÃO validado na Gupy real.
- [Thiago] `bot-vagas senha-gupy` uma vez. Se a conta da Gupy só tinha login por LinkedIn, criar senha em
  'Esqueceu a senha?'.
- Efeito colateral visto: clicar em 'Candidatar-se' logado já INSCREVE na vaga; a Gupy manda e-mail "ainda não
  finalizou a sua candidatura" (v(dev), 17/11). A simulação não envia, mas deixa a inscrição iniciada.

## (histórico) Decisão sobre a sessão do LinkedIn ("Entrar com LinkedIn" da Gupy)
- **A)** o bot clica em "Entrar" na tela do LinkedIn SÓ quando o Chromium já preencheu e-mail e senha sozinho
  (nunca lê, digita ou guarda senha); código/verificação/captcha => para e avisa. Risco recai sobre a conta do
  LinkedIn (verificação/suspensão por login automático).
- **B) (EM VIGOR)** o bot para, avisa no celular e o Thiago refaz `bot-vagas login-gupy <link>` (~1 min).
- Critério combinado (2026-11-17): decidir com os dados da semana de simulação. `sessao_sso.py` grava em
  `~/.local/share/bot-vagas/logs/sessoes.jsonl` (0600; só data, provedor, passou/caiu) e o `automatico` imprime
  "quedas nos últimos 7 dias". Sugestão: até 2 quedas/semana => fica B; quedas quase diárias => considerar A.
  A NÃO está implementada: só com decisão explícita do Thiago.

## Pendências (em ordem)
1. [Thiago] Semana de simulação na Gupy: `cp automacao.example.yaml automacao.yaml`, `bot-vagas login-gupy <link>`
   (logar também no LinkedIn se a Gupy mandar para lá),
   `bot-vagas automatico --agora --limite 1`; depois `ativo: true` para o timer. Trazer screenshots/log.
2. Calibrar RE_* do piloto e PADROES_CAMPOS com o que aparecer na simulação; só então `modo: envio`.
   No fim da semana: Thiago decide A ou B (sessão do LinkedIn) olhando as quedas registradas.
3. LinkedIn (Easy Apply): fonte de vagas (busca logada) + piloto próprio, limite 3/dia (§15.8). Precisa de
   sessão logada do Thiago e de calibração real como na Gupy.
4. Indeed: idem. Aviso: o Indeed costuma bloquear automação com desafio Cloudflare; pela regra de não evasão o bot
   vai parar e avisar quando isso acontecer, então o ganho pode ser pequeno.
- Continua FORA de escopo: resolver captcha; qualquer evasão de detecção.

## Regras que valem sempre
- Sem chamadas reais à API da Anthropic (nem nos testes). `--real` só com autorização explícita do Thiago e teto definido
  na conta dele; no modo automático a autorização é `usar_modelo_para_perguntas_abertas: true` em automacao.yaml (ele
  liga depois do teto). O código NÃO lê ANTHROPIC_API_KEY (o SDK lê sozinho).
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
