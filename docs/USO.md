# Manual de uso

Como instalar, configurar e operar o bot no dia a dia. Visão geral do projeto: [README](../README.md).
Decisões e motivos: [DECISOES.md](DECISOES.md).

Todos os comandos rodam na raiz do projeto.

## Instalação (clone limpo, Linux, Python 3.12)

```bash
git clone <este repositório> bot-vagas && cd bot-vagas
python3.12 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/playwright install chromium        # navegador usado pelo copiloto e pelo gerador de PDF

cp perfil.example.yaml perfil.yaml           # SEUS dados do currículo (fica fora do git)
cp respostas.example.yaml respostas.yaml     # respostas fixas de formulário (fora do git)
cp notificacao.example.yaml notificacao.yaml # opcional: aviso no celular (fora do git)

.venv/bin/pytest -q                          # todos os testes devem passar (sem rede, sem API)
```

Edite `perfil.yaml` e `respostas.yaml` com cuidado: **campo `null` = o bot não responde** e deixa para você.

## Primeira execução (passo a passo)

```bash
.venv/bin/bot-vagas curriculo                 # gera saida/curriculo-thiago-mateus-pt.pdf e -en.pdf
.venv/bin/bot-vagas rodar                     # busca + filtro (sem API paga, nada é enviado)
.venv/bin/bot-vagas listar --status nova      # vagas que passaram no filtro
.venv/bin/bot-vagas painel                    # http://127.0.0.1:8765 (vagas, status, gasto, botão PARAR)

.venv/bin/bot-vagas login-gupy                # Chromium visível: VOCÊ faz login; feche a janela ao terminar
.venv/bin/bot-vagas candidatar <link-da-vaga-na-gupy>   # copiloto: preenche/anexa/destaca; nunca clica
# ... você confere tudo e clica em Enviar na própria Gupy ...
.venv/bin/bot-vagas marcar <link-ou-chave> enviada      # só você diz quando enviou
```

## Comandos

| Comando | O que faz |
|---|---|
| `curriculo [--idioma pt\|en\|ambos]` | gera o PDF base (1 página, conferido automaticamente) |
| `rodar` | rodada diária: `buscar` + `filtrar`; notifica no celular se configurado. Usado pelo timer |
| `buscar` / `filtrar [--refazer]` | as duas etapas separadas |
| `avaliar --real` | nota do modelo (API **paga**). Sem `--real` recusa. Teto: `custos.yaml` |
| `listar [--status ...]` | tabela das vagas |
| `painel [--porta 8765]` | painel local (só 127.0.0.1) |
| `preparar <url>` ou `preparar --arquivo vaga.txt --empresa X --cargo Y` | PDF + pacote de respostas de UMA vaga |
| `login-gupy` | abre o Chromium com o perfil do bot para você logar |
| `candidatar <url\|chave> [--pdf x.pdf] [--sem-log]` | copiloto da Gupy (detalhes abaixo) |
| `marcar <url\|chave> enviada\|desistiu\|em_candidatura` | atualiza o status |
| `parar [motivo]` / `retomar` | **botão PARAR** (arquivo `PARAR` na raiz do projeto) |
| `gerar-timer [--hora 14:00]` | gera (não instala) o timer do systemd |
| `testar-notificacao` | manda um push de teste pelo ntfy |

### Copiloto (`candidatar`)

- Abre a vaga num Chromium **visível** com o perfil `~/.local/share/bot-vagas/navegador` (0700, onde fica sua
  sessão da Gupy). Você clica em "Candidatar-se", "Próximo" e "Enviar"; o bot só **preenche campos vazios** que
  reconhece, **anexa o PDF** e **destaca** (contorno laranja) o que é com você.
- Nunca toca em: login/senha/e-mail/captcha, caixas de consentimento/LGPD, perguntas abertas e testes.
- Termina quando você **fecha a janela**, aperta **Ctrl+C**, o sistema manda **SIGTERM** ou o **PARAR** é ligado.
  Em todos os casos imprime o **resumo** e grava a assistência no banco. Não marca `enviada` sozinho.
- Se outra janela do bot (ex.: um `login-gupy` esquecido) estiver aberta: mensagem clara e código de saída 3.
  Feche a outra janela e tente de novo.
- **Log de depuração** (para calibrar o reconhecimento de campos): `~/.local/share/bot-vagas/logs/candidatar-*.jsonl`
  (pasta 0700, arquivo 0600). Uma linha por campo visto: tipo, rótulo, categoria atribuída, preenchido/pendente/pulou e
  motivo. **Nunca** grava valores digitados, valores do `respostas.yaml` (são trocados por `[omitido]`), senhas,
  cookies ou query string de URL. `--sem-log` desliga.
- Se aparecer tela de login: avisa **uma vez por mudança de página** (e manda push, se configurado).

### Botão PARAR

Qualquer um destes liga o PARAR: `bot-vagas parar`, botão **Parar tudo** no painel, ou `touch PARAR`.
Enquanto o arquivo existir, `buscar`, `filtrar`, `avaliar`, `preparar`, `rodar`, `login-gupy` e `candidatar`
não começam (código de saída 4), e os que estão rodando param no próximo ponto seguro (entre fontes, entre vagas,
a cada ~1 s no copiloto). `listar`, `painel` e `marcar` continuam funcionando. Para liberar: `bot-vagas retomar`.

### Notificação no celular (opcional)

1. Instale o app **ntfy** e assine um tópico com nome longo e aleatório (quem souber o nome lê as mensagens).
2. Ponha o nome em `notificacao.yaml` (`ntfy.topico`) e rode `bot-vagas testar-notificacao`.

Mensagens fixas e genéricas: login pendente no copiloto, execução parada, alarme na busca, rodada concluída (só a
quantidade de vagas), erro. Nunca saem empresa, cargo, link ou dados pessoais.

## Agendamento (systemd --user, 14h)

```bash
.venv/bin/bot-vagas gerar-timer               # escreve saida/systemd/bot-vagas.service e .timer
mkdir -p ~/.config/systemd/user
cp saida/systemd/bot-vagas.* ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now bot-vagas.timer
systemctl --user list-timers bot-vagas.timer  # confira o próximo disparo
```

- `Persistent=true`: se o PC estava desligado/suspenso às 14h, a rodada roda quando ligar.
- Para rodar mesmo sem você logado: `loginctl enable-linger "$USER"`.
- Testar agora: `systemctl --user start bot-vagas.service && journalctl --user -u bot-vagas -n 50`.
- Desinstalar: `systemctl --user disable --now bot-vagas.timer`.
- A rodada agendada só faz `buscar` + `filtrar`: **não chama a API paga e não se candidata**.

## O que conferir pessoalmente

- **Currículo gerado** (`saida/*.pdf`): telefone, e-mail, links e datas. O verificador de fatos barra invenções,
  mas quem garante que o `perfil.yaml` está certo é você.
- **`respostas.yaml`**: pretensão, disponibilidade, dados voluntários. Deixe `null` o que preferir responder na hora.
- **Cada formulário antes de enviar**: o copiloto marca em verde o que preencheu e em laranja o que é com você.
  Confira os campos verdes também.
- **Primeiro uso do copiloto**: depois da candidatura, abra o log de depuração e veja quais campos ficaram como
  "não reconhecido"; isso mostra o que falta ensinar ao bot.
- **Timer**: confira com `systemctl --user list-timers` e, no primeiro dia, com `journalctl --user -u bot-vagas`.
- **Antes de qualquer `--real`**: defina o teto de gasto no painel da Anthropic (o bot também tem o próprio teto
  em `custos.yaml`, US$ 3/mês) e reconfirme os preços.

## Privacidade e segurança

- Dados pessoais só em `perfil.yaml`, `respostas.yaml` e no perfil do navegador (todos fora do git).
- O código nunca lê a chave da API (o SDK da Anthropic lê sozinho, e só com `--real`).
- Sem evasão de detecção de automação, sem resolver captcha, sem LinkedIn/Indeed. Se a Gupy bloquear, o bot para e avisa.
- `vagas.db` é o banco local; não edite à mão. Para ver o painel com dados fictícios: `.venv/bin/python demo/criar_demo.py`.
