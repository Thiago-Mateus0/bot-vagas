# bot-vagas — assistente de busca de vagas

Assistente em Python que **encontra vagas de estágio/trainee em tecnologia, filtra as que fazem sentido para o meu
perfil, gera um currículo adaptado a cada uma sem inventar nada e me ajuda a preencher o formulário** — mas quem
revisa e clica em "enviar" sou eu.

Projeto pessoal de [Thiago Mateus](https://github.com/Thiago-Mateus0), estudante de Ciência da Computação
(Afya UNIMA, Maceió/AL), construído para resolver um problema real: a minha própria busca pelo primeiro estágio.

> **Em uma frase:** menos tempo garimpando vaga, mais tempo nas vagas certas — com um humano no controle de cada envio.

---

## O que ele faz

| Etapa | Comando | O que acontece |
|---|---|---|
| 1. Buscar | `bot-vagas buscar` | Lê vagas de fontes públicas (Greenhouse, Lever e a busca pública da Gupy) e salva em SQLite. |
| 2. Filtrar | `bot-vagas filtrar` | Regras por palavras-chave: só estágio/trainee, remoto **ou** em Maceió/AL, bloqueia áreas fora do meu perfil. |
| 3. Avaliar | `bot-vagas avaliar --real` | Um LLM (Claude Haiku) dá nota de 0 a 10 de aderência; só passam as vagas acima do corte. |
| 4. Revisar | `bot-vagas painel` | Painel web local (somente leitura) com as vagas, notas, motivos e gasto com a API. |
| 5. Preparar | `bot-vagas preparar <url>` | Gera um currículo em PDF de 1 página adaptado à vaga + um pacote com as respostas do formulário. |
| 6. Candidatar | `bot-vagas candidatar <url>` | Copiloto: abre a vaga num navegador visível e preenche os campos que reconhece. **Nunca clica nem envia.** |
| 7. Registrar | `bot-vagas marcar <vaga> enviada` | Eu informo o que enviei; o bot nunca se candidata duas vezes à mesma vaga. |

```mermaid
flowchart LR
  A[Fontes públicas] --> B[Filtro por regras]
  B --> C[Nota do LLM]
  C --> D[Painel: eu escolho]
  D --> E[Currículo adaptado + verificador de fatos]
  E --> F[Copiloto preenche]
  F --> G((Eu reviso e envio))
```

## Decisões éticas (e por quê)

Automatizar candidatura é fácil de fazer mal. Estas foram decisões conscientes:

- **Humano no controle de todo envio.** O copiloto preenche e destaca campos, mas não tem nenhum `.click()` ou
  submit no código — isso é verificado por teste e por um revisor automático. Eu leio e envio.
- **Qualidade em vez de volume.** Filtro por regras + nota do modelo: o bot descarta a maioria das vagas. Com os
  filtros atuais sobram de 1 a 3 vagas por semana — candidaturas que fazem sentido, não spam para recrutadores.
- **Nada inventado.** O currículo pode ser reordenado e o resumo reescrito, mas só com fatos que já existem no meu
  perfil. Um **verificador de fatos** determinístico confere a adaptação proposta pelo modelo e rejeita qualquer
  habilidade, data, número ou termo que não esteja no perfil; se reprovar, sai o currículo neutro. Pergunta de formulário sem resposta conhecida fica em branco para eu responder.
- **Fontes que aceitam leitura automática, com respeito aos limites.** LinkedIn e Indeed ficaram **fora** porque
  os termos deles proíbem automação. Greenhouse e Lever têm APIs públicas. Na Gupy o bot lê só a página pública de
  busca, com pausa mínima entre requisições, teto por execução, e **para** em caso de bloqueio, captcha ou HTTP
  429 — sem disfarçar automação (nada de stealth ou user-agent falso). Ponto de atenção honesto: essa página não é
  uma API documentada, e a escolha está registrada como risco em [`REQUISITOS.md`](REQUISITOS.md).
- **Privacidade.** Dados pessoais ficam só em dois arquivos locais fora do git (`perfil.yaml`, `respostas.yaml`)
  e nunca vão para o modelo, para logs ou para o repositório.
- **Custo controlado.** Chamadas pagas só com a flag explícita `--real`, e um contador interno para o bot antes de
  chegar ao teto de US$ 3/mês.

## Destaques técnicos

- **Python 3.12**, CLI com `argparse`, modelos de dados com **Pydantic**, persistência em **SQLite** com estado
  retomável (se o PC desligar no meio, não aplica duas vezes).
- **Painel** com **FastAPI + Jinja2**, servido só em `127.0.0.1`.
- **Playwright** para o copiloto (navegador visível, perfil persistente, sem automação de clique).
- **LLM (Claude Haiku)** com prompt fixo em cache e esforço baixo para reduzir custo; toda saída do modelo passa
  pelo verificador de fatos antes de virar PDF.
- **238 testes** com `pytest`, sem rede e sem chamada paga: as fontes usam fixtures salvas, o banco é temporário e
  o cliente da API é falso nos testes.
- Desenvolvido com apoio de agentes de IA (Claude Code), com guarda-corpos no próprio repositório: um
  [`CLAUDE.md`](CLAUDE.md) com as regras, hooks que bloqueiam ações proibidas (ler dados pessoais, chamada paga,
  commit sem minha aprovação) e rodam os testes a cada edição, e um subagente revisor independente.

## Rodando localmente

```bash
git clone https://github.com/Thiago-Mateus0/bot-vagas && cd bot-vagas
python3.12 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/playwright install chromium

# Seus dados (ficam só na sua máquina; ambos estão no .gitignore)
cp perfil.example.yaml perfil.yaml
cp respostas.example.yaml respostas.yaml
# edite os dois com as suas informações

.venv/bin/pytest -q            # deve passar tudo
.venv/bin/bot-vagas buscar     # busca vagas (rede)
.venv/bin/bot-vagas filtrar
.venv/bin/bot-vagas listar
.venv/bin/bot-vagas painel     # abre em http://127.0.0.1:8765
```

Sem `--real`, nenhuma chamada paga é feita: a avaliação pelo modelo é pulada e o currículo sai na versão neutra.
Para usar o LLM, defina `ANTHROPIC_API_KEY` no ambiente (o SDK lê sozinho) e um teto de gasto na sua conta da
Anthropic antes.

**Na primeira execução, confira:**
- `perfil.yaml` e `respostas.yaml` com os seus dados reais (o bot não gera currículo com o exemplo).
- O PDF em `saida/` cabe em 1 página e não tem nada que você não escreveu.
- No copiloto, todo campo preenchido antes de clicar em enviar — campos sem resposta ficam pendentes de propósito.

Filtros, termos de busca e fontes são configuráveis em [`filtros.yaml`](filtros.yaml) e [`fontes.yaml`](fontes.yaml).

## Estrutura

```
src/bot_vagas/
  cli.py            comandos
  fontes/           Greenhouse, Lever, Gupy
  filtro.py         fase A: regras por palavras-chave e localização
  avaliador.py      fase B: nota do LLM
  curriculo.py      PDF de 1 página (PT/EN)
  verificador.py    bloqueia fatos que não estão no perfil
  preparar.py       pacote de candidatura de uma vaga
  copiloto.py       preenchimento assistido na Gupy (sem clique)
  painel.py         painel web local
  gasto.py          contador de custo da API
tests/              238 testes, sem rede
```

## Limitações e próximos passos

- A Gupy concentra a maioria das vagas locais, mas não tem API pública: se a página mudar, o bot avisa e para.
- Em andamento: encerramento limpo do copiloto ao fechar o navegador, botão PARAR, notificação opcional
  (ntfy.sh) e agendamento diário com `systemd --user` timer. O estado está em [`HANDOFF.md`](HANDOFF.md).

## Resultado até agora

Primeira candidatura real enviada com o assistente: estágio em Governança de TI na CCEE.
