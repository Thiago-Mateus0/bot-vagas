# bot-vagas

Bot pessoal de candidaturas: busca vagas de **estágio/trainee** (remoto ou Maceió/AL), filtra, gera o currículo
em PDF de 1 página sem inventar nada e se candidata na Gupy de dois jeitos:

- **modo automático** (`bot-vagas automatico`): o bot clica, preenche, aceita os termos e envia sozinho.
  Começa em **simulação** (para na última tela, sem enviar) até você mudar `modo: envio` em `automacao.yaml`;
- **copiloto** (`bot-vagas candidatar`): o bot preenche e você clica em tudo.

O que ele faz e o que ele nunca faz:

| Faz | Nunca faz |
|---|---|
| Busca em Greenhouse, Lever, na busca pública da Gupy e no quadro velrino/jobs (GitHub) | LinkedIn/Indeed, captcha, contornar bloqueio |
| Filtra por palavras-chave (`filtros.yaml`) | Chamar a API paga sem `--real` |
| Gera currículo PT/EN de 1 página a partir de `perfil.yaml` | Inventar habilidade, empresa, data ou cargo |
| Modo automático na Gupy: clica, preenche, aceita termos/LGPD, envia | Resolver captcha, disfarçar o robô (stealth/UA falso) |
| Copiloto da Gupy: preenche e anexa; você clica | No copiloto: clicar, enviar, marcar aceite/LGPD |
| Responde pergunta aberta com o Haiku (se você ligar), checada contra o perfil | Inventar dado pessoal ou experiência |
| Painel local, contador de gasto, botão PARAR, aviso no celular | Mandar dados pessoais ao modelo, a logs ou ao git |

## Instalação (clone limpo, Linux, Python 3.12)

```bash
git clone <url-do-repo> ~/bot-vagas      # os arquivos de systemd/ assumem esta pasta
cd ~/bot-vagas
python3.12 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/playwright install chromium     # navegador usado para PDF e copiloto

# dados pessoais: ficam SÓ nestes arquivos locais (estão no .gitignore)
cp perfil.example.yaml perfil.yaml        # seu currículo estruturado (fonte única da verdade)
cp respostas.example.yaml respostas.yaml  # respostas fixas de formulário; null = o bot não responde

.venv/bin/pytest -q                       # tudo deve passar (com ou sem perfil.yaml)
```

Para não digitar `.venv/bin/` toda hora: `source .venv/bin/activate` e use `bot-vagas ...` direto.

## Primeira execução (nesta ordem)

1. **Edite `perfil.yaml`** e confira e-mail, telefone, LinkedIn e GitHub.
2. **Gere o currículo base** e leia os PDFs: `bot-vagas curriculo` (saem em `saida/`).
3. **Edite `respostas.yaml`** (pretensão, disponibilidade, cidade...). Deixe `null` no que você prefere responder na hora.
4. **Busque e filtre** (rede, sem API paga): `bot-vagas rodar`, e depois `bot-vagas listar` ou o painel.
5. **Painel**: `bot-vagas painel` e abra http://127.0.0.1:8765/
6. **Login na Gupy** (uma vez; a sessão fica no perfil local do navegador): `bot-vagas login-gupy`.
   Faça login você mesmo e feche a janela.
7. **Candidatar com o copiloto**: `bot-vagas candidatar <link da vaga na gupy.io>`.
   Você clica em "Candidatar-se", "Próximo" e "Enviar"; o bot preenche e destaca o resto.
   Feche a janela (ou Ctrl+C) para terminar: o resumo é impresso e gravado.
8. **Diga ao bot o que você fez**: `bot-vagas marcar <chave|url> enviada` (ou `desistiu`).

## Comandos

| Comando | O que faz |
|---|---|
| `bot-vagas curriculo [--idioma pt\|en]` | PDFs do currículo base em `saida/` |
| `bot-vagas buscar` | baixa vagas das fontes de `fontes.yaml` (rede) |
| `bot-vagas filtrar [--refazer]` | filtro por palavras-chave (sem rede) |
| `bot-vagas rodar` | `buscar` + `filtrar` (+ `automatico` se `ativo: true`) — é o que o timer das 14h executa |
| `bot-vagas avaliar --real` | nota 0–10 pelo Haiku 5.5. **Pago.** Sem `--real` recusa |
| `bot-vagas listar [--status ...]` | tabela das vagas |
| `bot-vagas painel` | painel local (somente leitura + botão PARAR) |
| `bot-vagas preparar <url> [--real]` | pacote da candidatura (PDF + respostas); não envia nada |
| `bot-vagas login-gupy` | Chromium visível para você logar na Gupy |
| `bot-vagas automatico [--agora] [--simular] [--limite N]` | candidata-se sozinho (simulação ou envio, ver `automacao.yaml`) |
| `bot-vagas candidatar <url\|chave> [--sem-log]` | copiloto do formulário; nunca clica nem envia |
| `bot-vagas marcar <chave\|url> enviada\|desistiu\|em_candidatura` | registra o que você fez |
| `bot-vagas marcar <chave\|url> fila` | devolve ao modo automático uma vaga pulada por engano |
| `bot-vagas parar` / `bot-vagas retomar` | liga/desliga o botão PARAR |
| `bot-vagas gerar-timer` | gera os arquivos de systemd em `systemd/` (não instala) |
| `bot-vagas testar-notificacao` | manda um aviso de teste pelo ntfy |

Códigos de saída: `0` ok · `1` erro/alarme de fonte, ou modo automático parado por login/captcha/bloqueio · `2` recusado · `3` perfil do navegador em uso ·
`4` parado pelo PARAR · `130` interrompido (Ctrl+C/SIGTERM).

## Botão PARAR

Crie o arquivo `PARAR` na raiz do projeto (`bot-vagas parar`, `touch PARAR` ou o botão vermelho do painel).
Qualquer execução para no próximo ciclo (entre fontes, entre vagas, antes de cada chamada paga, a cada
varredura do copiloto) e nenhuma nova começa. O copiloto fecha a janela, imprime e grava o resumo.
Para liberar: `bot-vagas retomar` (ou apague o arquivo, ou o botão "Retomar" do painel).

## Modo 100% automático (Gupy)

Você aceitou o risco de banimento da conta. Configuração em `automacao.yaml` (fora do git):

```bash
cp automacao.example.yaml automacao.yaml
bot-vagas login-gupy "<link de vaga>"   # uma vez: a sessão fica no perfil do navegador do bot
bot-vagas automatico --agora --limite 1 # primeiro teste: 1 vaga, em SIMULAÇÃO
```

O que ele faz em cada vaga da Gupy que passou no filtro: abre a vaga, clica em "Candidatar-se"/"Próximo",
preenche com `respostas.yaml`, anexa o PDF, marca termos/LGPD obrigatórios (nunca newsletter/marketing) e:

- `modo: simulacao` (padrão): **para no botão final** ("Enviar/Finalizar candidatura"), tira screenshot em
  `saida/capturas/` e marca a vaga como `simulada`. **Nada é enviado.**
- `modo: envio`: clica no botão final, espera a confirmação da Gupy, tira screenshot e marca `enviada` sozinho.
  Se clicar e não vir a confirmação, marca `em_candidatura` com "confira na Gupy" e **não tenta de novo**.

Ele **pula a vaga** (status `pulada`, motivo no painel) quando: há campo obrigatório que não sabe (ex.: CPF),
pergunta aberta sem resposta segura, a candidatura continua fora da Gupy, não acha o botão, fica travado na
mesma tela ou você já se candidatou. Ele **para a rodada** e avisa no celular em: tela de login, captcha,
sinal de bloqueio, PARAR ou Ctrl+C. Nunca resolve captcha nem disfarça o robô.

**Semana de simulação (recomendado):** rode com `modo: simulacao` por uma semana, abra os screenshots e o log
`~/.local/share/bot-vagas/logs/automatico-*.jsonl` (quais botões e campos ele viu). Quando estiver bom, troque
para `modo: envio`. As vagas `simulada` voltam a ser candidatas e são enviadas na próxima rodada.

**Perguntas abertas com o Haiku:** desligado por padrão. Depois de definir o teto no painel da Anthropic, ponha
`usar_modelo_para_perguntas_abertas: true`. O modelo recebe só o perfil (sem contato) e a pergunta/vaga; a resposta
passa pelo verificador de fatos e, se citar algo que não está no perfil, a vaga é pulada. Cada pergunta custa uma
chamada (contada no teto de US$ 3/mês do bot).

**Limites:** `limite_diario`, `limite_por_site`, janela `14:00–17:00` (fora dela ele não começa; `--agora` força)
e pausa aleatória de 2 a 7 minutos entre candidaturas. Para o timer se candidatar sozinho: `ativo: true`.

**Login (Gupy e "Entrar com LinkedIn"):** cada empresa da Gupy tem o próprio endereço (`empresa.gupy.io`) e o
"Candidatar-se" pode mandar para o login da Gupy ou do LinkedIn. Faça o login uma vez pelo caminho que o bot usa:

```bash
bot-vagas login-gupy "<link de uma vaga da Gupy>"   # clique em Candidatar-se, logue (no LinkedIn também, se
                                                    # aparecer, com "lembrar"), e feche quando surgir o formulário
```

Com o LinkedIn logado no navegador do bot, a Gupy passa sozinha pelo LinkedIn nas outras empresas. Se a sessão
cair, o bot **para a rodada** (não marca a vaga como pulada), avisa no celular e você refaz o comando acima. O bot
nunca digita nem clica em "Entrar", mesmo que o Chromium tenha preenchido a senha: essa é uma decisão sua ainda
em aberto (ver HANDOFF.md). No fim de cada `automatico` aparece a linha "sessão do LinkedIn: ... quedas nos últimos
7 dias: N" para você decidir com dados.

**Login automático (decisão de 17/11):** guarde o login (e-mail ou CPF) e a senha **da Gupy** no chaveiro do
Linux, uma vez: `bot-vagas senha-gupy` (nada aparece na tela; `--verificar` diz se há algo guardado e
`--apagar` apaga). Com `digitar_senha_gupy: true`, quando a Gupy pedir login o bot digita e clica em
"Acessar conta", só em gupy.io e no máximo uma vez por rodada. Se pedir código, verificação ou captcha, ou se a
senha não funcionar, ele para e avisa. O chaveiro precisa estar desbloqueado (você logado no computador).

**Atenção, simulação:** clicar em "Candidatar-se" logado já inicia a inscrição na Gupy. A simulação não envia,
mas a Gupy pode mandar e-mail "você ainda não finalizou a sua candidatura". É esperado.

(Alternativa antiga, não recomendada: sessão do LinkedIn caía em ~1 h.) Se a **Gupy** pedir login, `entrar_com_linkedin: true` no
`automacao.yaml`: o bot clica só no botão "LinkedIn" dessa tela e o LinkedIn entra com a sessão salva. Ele não
toca nos campos de e-mail/CPF/senha.

**Fonte velrino/jobs:** o `fontes.yaml` lê o quadro https://github.com/velrino/jobs (estágios no Brasil, já
classificados). Vagas da Gupy de lá entram no modo automático; as de outros sites (InHire, Sólides...) aparecem
no painel para você se candidatar à mão. A mesma vaga vinda de duas fontes não é duplicada.

**A Gupy fecha a aba depois do login pelo LinkedIn:** o bot mantém uma aba em branco aberta (não feche) para o
navegador não fechar junto, e reabre a vaga sozinho.

**Não feche a janela do bot durante a rodada** (nem na pausa entre vagas): se fechar, a rodada para com
"janela fechada" e as vagas restantes ficam na fila.

**Vaga pulada por engano:** `bot-vagas marcar <link> fila` devolve a vaga ao modo automático.

**Screenshots:** `saida/capturas/` (fora do git, permissão 0600). Eles mostram seus dados (formulário preenchido,
seu e-mail na tela de login): não compartilhe sem olhar.

**LinkedIn e Indeed:** ainda não. Próxima etapa, depois da semana de simulação da Gupy (ver HANDOFF.md).

## Copiloto da Gupy: o que esperar

- Se aparecer `ERRO: Já existe um navegador usando o perfil do bot...`, feche a outra janela
  (`login-gupy` ou outro `candidatar`) e rode de novo.
- "PRECISA DE LOGIN" aparece uma vez por página de login: faça login na própria janela; o bot espera.
- **Log de depuração** (para calibrar o reconhecimento de campos):
  `~/.local/share/bot-vagas/logs/candidatar-AAAAMMDD-HHMMSS.jsonl`, permissão 0600. Uma linha por campo:
  rótulo, tipo, categoria, se preencheu/pendente/pulou e por quê. **Nunca** valores, senhas, e-mails, cookies
  ou texto da vaga (textos iguais a valores de `respostas.yaml`/contato do perfil viram `[omitido]`).
  `--sem-log` desliga. Abra o arquivo antes de mostrar a alguém, por garantia.

## Aviso no celular (opcional, desligado por padrão)

```bash
cp notificacao.example.yaml notificacao.yaml   # fora do git
# edite: ntfy.ativo: true e um tópico longo e aleatório; assine o mesmo tópico no app ntfy
bot-vagas testar-notificacao
```

Avisa quando o copiloto pausa pedindo login/captcha, quando uma fonte bloqueia ou muda de formato e quando o
teto de gasto é atingido. O texto é sempre genérico (sem empresa, vaga, link ou dados seus). O tópico do ntfy
funciona como senha: quem souber o nome lê os avisos.

## Agendamento diário às 14h (systemd --user)

Os arquivos já estão em `systemd/` (regere com `bot-vagas gerar-timer` se o projeto não estiver em `~/bot-vagas`).
Nada é instalado pelo bot. Para instalar **no seu PC**:

```bash
mkdir -p ~/.config/systemd/user
cp systemd/bot-vagas.service systemd/bot-vagas.timer ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now bot-vagas.timer
systemctl --user list-timers bot-vagas.timer        # mostra o próximo disparo
journalctl --user -u bot-vagas.service -n 50        # saída da última rodada
```

- A rodada é `bot-vagas rodar`: busca e filtra; com `ativo: true` em `automacao.yaml`, também se candidata
  (simulação ou envio, conforme `modo`). Sem `usar_modelo_para_perguntas_abertas: true`, não usa a API paga.
- Para o navegador abrir visível pelo timer, a sessão gráfica precisa estar ativa; sem tela, ele roda sem janela
  (`navegador_visivel: auto`). Se a Gupy pedir login, ele para e avisa: rode `bot-vagas login-gupy`.
- `Persistent=true`: se o PC estava desligado ou suspenso às 14h, a rodada acontece quando ele voltar.
- Timers de usuário só rodam com você logado. Se quiser que rodem sem login: `loginctl enable-linger $USER`.
- Testar já, sem esperar as 14h: `systemctl --user start bot-vagas.service`.
- Desinstalar: `systemctl --user disable --now bot-vagas.timer` e apague os dois arquivos de `~/.config/systemd/user/`.

## Custos (API da Anthropic)

Só `avaliar --real` e `preparar --real` chamam a API (Haiku 5.5). Antes do primeiro `--real`:

1. **Defina o teto de gasto no painel da Anthropic** (o bot tem um contador próprio com teto de US$ 3/mês em
   `custos.yaml`, mas o teto da conta é a garantia de verdade).
2. Reconfirme os preços em `custos.yaml`.
3. O SDK lê a chave da variável `ANTHROPIC_API_KEY`; o código do bot nunca a lê nem a grava.

## O que conferir pessoalmente

- [ ] Os PDFs em `saida/`: 1 página, links, telefone e e-mail corretos, nada inventado.
- [ ] `respostas.yaml`: cada valor é o que você quer mandar para qualquer empresa.
- [ ] Semana de simulação: os screenshots em `saida/capturas/` (campos certos? termos certos? parou no botão certo?).
- [ ] Antes de `modo: envio`: o log `automatico-*.jsonl` não pode ter nenhum dado seu.
- [ ] Depois de ligar o envio: as primeiras candidaturas na própria Gupy ("Minhas candidaturas").
- [ ] No copiloto: **todo campo destacado em verde** ("confira") antes de clicar em Enviar; os laranja são seus.
- [ ] No copiloto, depois de enviar: `bot-vagas marcar ... enviada` (no modo automático o bot marca sozinho).
- [ ] O primeiro log de depuração em `~/.local/share/bot-vagas/logs/`: não deve ter nenhum dado seu.
- [ ] `git status` antes de qualquer commit: `perfil.yaml`, `respostas.yaml`, `notificacao.yaml`, `automacao.yaml`,
      `vagas.db`, `saida/` e `PARAR` nunca devem aparecer.

## Testes

```bash
.venv/bin/pytest -q
```

Sem rede externa, sem API, sem a Gupy real e sem o perfil real do navegador: páginas fixture em 127.0.0.1,
Chromium headless com perfis temporários, e `PARAR`/`notificacao.yaml`/logs redirecionados para pastas
temporárias (ver `tests/conftest.py`). Sem `perfil.yaml`, os testes usam `perfil.example.yaml`.
