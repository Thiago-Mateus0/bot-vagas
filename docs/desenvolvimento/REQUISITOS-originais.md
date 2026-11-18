> Levantamento ORIGINAL (2026-11-11), mantido como histórico. Várias decisões mudaram depois
> (ex.: o envio deixou de ser automático; LinkedIn e Indeed saíram do escopo). O que vale hoje está em `docs/DECISOES.md`.

# Bot de candidaturas — requisitos

Status: etapas 1 a 5 construídas (envio sempre manual, pelo copiloto). Atualizado em 2026-11-18; ver HANDOFF.md e README.md.
Mudança de decisão já registrada no HANDOFF: o envio NÃO é automático (§4, §15): o bot prepara e preenche, Thiago clica em Enviar.
O Fable low é usado só para CONSTRUIR o bot. O modelo que o bot usa em produção é outro (Haiku 5.5).

## 1. Objetivo
Achar vagas, adaptar o currículo de cada uma sem inventar nada e se candidatar automaticamente.

## 2. Perfil das vagas
- Níveis: **estágio e trainee** (decidido em 2026-11-11: vagas júnior excluídas por ora; Thiago as tenta manualmente depois de entrar em um estágio/trainee). "Graduate/new grad" também fora (exigem curso concluído). O nível é procurado só no título.
- Remoto, OU presencial/híbrido em **Maceió, AL** (atualizado em 2026-11-11; antes era só remoto). Presencial em outras cidades continua bloqueado.
- Áreas prioritárias: **Backend** e **IA aplicada (integração com LLMs, automação, agentes)**.
- Bloquear ML clássico e ciência de dados (o currículo não tem essa experiência; a Mérida é integração de LLM, não ML).
  - Bloqueiam: machine learning, ML engineer, data scientist, estatística, PyTorch, TensorFlow, scikit-learn, pandas.
  - Aumentam a nota: LLM, API de IA, prompt, integração, automação, agentes, backend, PostgreSQL, FastAPI, Node.js.
- Bloqueio extra: só palavras óbvias (vendas, telemarketing, vagas não remotas).

## 3. Sites
LinkedIn, Gupy, Indeed/Catho/InfoJobs, ATS de empresas (Greenhouse, Lever).
Risco aceito: LinkedIn e Indeed proíbem automação; possível bloqueio da conta. (Decisão revista depois: ambos ficaram fora do escopo.)

## 4. Autonomia e volume
- Totalmente automático, sem revisão por candidatura.
- 5 a 10 candidaturas por dia, com pausas entre elas e em horário comercial.
- Rodada diária curta (20 min a 1 h), sem precisar do PC ligado o dia todo.

## 5. Currículo
- Bases: `curriculo-thiago-mateus (3).pdf` (PT) e `curriculo-thiago-mateus-en.pdf` (EN). A versão "(2)" está obsoleta.
- O bot escolhe o idioma pelo idioma da vaga.
- Pode: reordenar, destacar, reescrever o resumo usando só fatos já presentes.
- Não pode: inventar habilidade, empresa, data, cargo, curso ou resultado.
- Fatos imutáveis: datas, empresa, cargo, curso/semestre, idiomas, o que cada projeto faz.
- O PDF gerado tem sempre 1 página (verificação automática). As bases já ocupam quase a página inteira.
- Fonte dos dados: arquivo estruturado (YAML/JSON) criado a partir dos PDFs; o PDF é só a saída.
- A Mérida deve ser descrita como "assistente com LLM integrado ao sistema em produção".

## 6. Formulários
- Respostas fixas definidas por Thiago antes do uso (pretensão salarial, disponibilidade, gênero, raça, PCD etc.).
- Pergunta desconhecida: pular a vaga e registrar (nunca inventar dado pessoal).

## 6b. Salário e exterior (decidido em 2026-11-11)
- Salário: o bot NÃO filtra vagas por salário (bolsas de estágio podem ficar abaixo da pretensão). Só informa o valor quando o formulário pedir.
- Exterior: apenas vagas remotas, como contratado (PJ), em qualquer país. Sem visto de trabalho. Vagas "remote – US only" etc. continuam bloqueadas, pois exigem residência/autorização no país.
- Fontes: lista inicial pesquisada e testada em 2026-11-11 (`fontes.yaml`). Vagas locais de Maceió estão sobretudo em Gupy, BNE, Sine e CIEE, que o bot ainda não lê.
- Respostas pessoais ficam em `respostas.yaml` (local, no .gitignore, nunca enviado ao modelo).

## 7. Filtro de vagas
1. Palavras-chave (bloqueio e pontuação).
2. Nota de 0 a 10 do modelo, com corte mínimo configurável.

## 8. Modelo e custo
- API da Anthropic, Haiku 5.5 (US$ 0,10 / US$ 0,50 por 1M tokens, preço cacheado em 2026-10-06; reconfirmar).
- Estimativa: cerca de US$ 1 a 3 por mês.
- Teto: US$ 3 por mês, configurado na conta da Anthropic (feito por Thiago) E contador de gasto no próprio bot, que para com aviso perto do teto.
- Usar esforço baixo e cache do prompt fixo para reduzir tokens.
- Sem VPS, sem proxy, sem serviço de captcha.

## 9. Execução
- No PC de Thiago, manual ou com timer agendado (no Linux, systemd timer com Persistent=true).
- Salvar estado: se o PC suspender no meio, retomar sem aplicar duas vezes na mesma vaga.
- Rede instável: tentar de novo ou pular a vaga.

## 10. Login, captcha e verificação
Ao esbarrar em login, captcha ou 2FA: pausar e avisar. Não usar serviço pago de captcha.

## 11. Acompanhamento
Painel web local: vaga, link, nota, currículo usado, status, gasto acumulado.

## 12. O que Thiago precisa fazer
- Definir o teto de gasto no painel da Anthropic.
- Escrever as respostas fixas dos formulários.
- Ler os primeiros currículos gerados antes de confiar no modo automático.
- Conferir links, telefone e e-mail do currículo.
- Aceitar o risco de ban nas contas.

## 13. Stack (decidida)
| Peça | Escolha |
|---|---|
| Linguagem | Python 3.12 |
| Navegador | Playwright (Python): preenche formulários, mantém login salvo, tira screenshot |
| Banco | SQLite |
| Modelo | SDK `anthropic`, Haiku 5.5, saída estruturada validada com pydantic |
| Currículo | Dados em YAML + modelo HTML; PDF gerado pelo Playwright; páginas contadas com `pypdf` |
| Painel | FastAPI + HTML simples (HTMX), sem React |
| Agendamento | `systemd` timer às 14h com `Persistent=true` + comando manual (`bot rodar`) |
| Notificação | ntfy.sh (gratuito) |

## 14. Horário e filtro
- Rodada a partir das 14h, ou manual quando Thiago acionar.
- As candidaturas se espalham entre 14h e 17h, com pausas aleatórias de alguns minutos.
- Nota de corte inicial: 7 de 10, calibrada na primeira semana (ver modo simulação).

## 15. Recursos adicionais (todos incluídos)
1. **Modo simulação (dry-run):** gera o currículo e preenche o formulário, mas não envia. É obrigatório na primeira semana e depois fica como opção. Nesse período o bot só avalia e mostra as notas; Thiago revisa cerca de 30 vagas e ajusta o corte.
2. **Verificador de fatos:** depois de gerar o currículo, compara cada afirmação com o YAML e rejeita o que não existir lá. Se rejeitar, a vaga é pulada e registrada.
3. **Sites seguros primeiro:** Gupy, Greenhouse e Lever antes; LinkedIn e Indeed só quando o restante estiver estável.
4. **Sem duplicatas:** guarda um identificador (empresa + cargo + link normalizado) e nunca aplica duas vezes na mesma vaga entre sites.
5. **Botão de parada:** arquivo `PARAR` ou botão no painel interrompe tudo imediatamente, inclusive no meio de uma rodada.
6. **Screenshot de cada envio:** salva a imagem do formulário preenchido e o PDF usado, ligados ao registro da vaga.
7. **Aviso no celular:** ao pausar por captcha, login ou 2FA, envia notificação via ntfy.sh.
8. **Limite diário e por site:** total de 5 a 10 por dia; no máximo 3 por dia no LinkedIn, valor configurável por site.

## 16. Ordem de construção
1. Perfil em YAML + gerador de currículo com verificador de fatos.
2. Busca e filtro de vagas com nota do modelo (somente leitura, sem enviar).
3. Painel + contador de gasto.
4. Aplicar em Gupy e Greenhouse/Lever, primeiro em modo simulação.
5. Agendamento, botão de parada e notificações.
6. LinkedIn e Indeed, só se tudo acima estiver estável.
Cada etapa termina com testes e com Thiago conferindo o resultado antes de passar para a próxima.

## 16b. Etapa 5 (concluída em 2026-11-18)
- Agendamento: `bot-vagas gerar-timer` gera `bot-vagas.service`/`.timer` (14h, Persistent=true); a rodada agendada
  (`bot-vagas rodar`) só busca e filtra, sem API paga e sem candidatura. Instalação fica com o Thiago (README).
- Botão de parada: arquivo `PARAR` (CLI `parar`/`retomar`, botão no painel).
- Notificação: ntfy.sh opcional, mensagens fixas sem dados pessoais (login pendente, parado, alarme, rodada, erro).

## 17. Decisões em aberto
- Nota de corte final (ajustada após a primeira semana de simulação).
- Lista exata de palavras-chave de bloqueio e pontuação (refinar com vagas reais).
