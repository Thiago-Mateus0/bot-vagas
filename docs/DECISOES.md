# Decisões de projeto

O que o bot faz, o que ele **não** faz e por quê. O levantamento original, de antes da construção, está em
`docs/desenvolvimento/REQUISITOS-originais.md`. Várias decisões mudaram ao longo do caminho, e as mudanças estão
explicadas aqui.

## Objetivo

Reduzir o trabalho repetitivo de procurar vagas e preencher formulários **sem perder o controle** sobre o que é
enviado. O bot encontra, filtra, prepara e preenche. A decisão de se candidatar, e o clique em "Enviar", são
sempre de uma pessoa.

## Escopo das vagas

- Níveis: **estágio e trainee**. O nível é procurado só no título da vaga.
- Local: **remoto**, ou presencial/híbrido em uma cidade configurada. Presencial em outras cidades é bloqueado.
- Vagas no exterior: só remotas e como contratado, sem exigência de visto ou residência ("remote, US only" é bloqueada).
- Áreas que aumentam a nota: backend e IA aplicada (integração com LLM, automação, agentes).
- Áreas bloqueadas: ML clássico e ciência de dados, porque o currículo não tem essa experiência e o bot não
  deve sugerir vagas para as quais o candidato não está pronto.
- Salário **não** é usado como filtro: bolsas de estágio variam muito, e o valor só é informado quando o formulário pede.

## Fontes

| Fonte | Como | Observação |
|---|---|---|
| Greenhouse, Lever | APIs públicas de quadros de vagas | quase não têm vagas de estágio no Brasil |
| Gupy | página pública de busca do portal | é a fonte principal; a rota não é documentada e pode mudar. O bot **detecta mudança de formato ou bloqueio, avisa e para**; não tenta contornar |

**Fora do escopo:** LinkedIn e Indeed. Os termos de uso proíbem automação, e o risco de perder a conta não compensa.

## Por que semi-automático (copiloto) e não envio automático

A ideia inicial era candidatura 100% automática, e eu cheguei a construí-la (em simulação por padrão, com a senha só
no chaveiro do sistema e parada obrigatória em captcha). Ela fica **fora deste repositório público**, porque:

1. **Erro caro e irreversível:** um formulário enviado com uma resposta errada não pode ser desfeito.
2. **Formulários variam muito:** perguntas abertas, testes e caixas de consentimento exigem julgamento humano.
3. **Termos de uso e captcha:** automação total levaria a contornar proteções. O bot não faz evasão de detecção
   (nada de "stealth", user agent falso ou mexer em `navigator.webdriver`) e não resolve captcha.

O resultado é o **copiloto**: ele abre a vaga num navegador visível, preenche só campos vazios que reconhece com
segurança, anexa o currículo e destaca o resto. Ele **nunca clica** (nem em "Próximo", nem em "Enviar"), nunca
marca consentimento e nunca toca em login, senha ou captcha. Há testes que verificam que nenhum evento de clique
ou submit é disparado.

## Currículo sem invenção

- Os dados ficam num YAML estruturado (`perfil.yaml`); o PDF é só a saída.
- O LLM pode reordenar, destacar e reescrever o resumo, **só com fatos que já estão no YAML**.
- **Verificador de fatos:** depois da geração, cada termo é conferido contra o perfil. Se aparecer habilidade,
  empresa, data ou resultado que não existe, a versão adaptada é rejeitada e o bot usa a versão neutra.
- O PDF tem sempre **1 página**, o que é verificado automaticamente (contagem com `pypdf`).
- O idioma do currículo (PT/EN) segue o idioma da vaga.

## Modelo e custo

- O modelo é o Claude Haiku via API da Anthropic, com esforço baixo, saída estruturada validada com pydantic e cache do prompt fixo.
- Toda chamada paga exige a flag explícita `--real`. Sem ela, o comando recusa e explica.
- **Teto de US$ 3/mês** em dois lugares: na conta da Anthropic e num contador próprio (`custos.yaml`), que
  bloqueia novas chamadas perto do teto. O painel mostra o gasto do mês.
- Os testes **nunca** chamam a API: usam clientes falsos, e um teste garante que o código não lê a chave da API.

## Privacidade

- Dados pessoais ficam só em arquivos locais fora do git: `perfil.yaml`, `respostas.yaml` e o perfil do navegador
  (pasta 0700).
- Dados de contato e respostas de formulário **nunca** vão para o modelo.
- O log de depuração do copiloto registra só rótulos e decisões. Valores iguais a respostas pessoais são trocados
  por `[omitido]`, e há teste de não vazamento.
- O painel escuta só em 127.0.0.1, valida o header Host (contra DNS rebinding), escapa todo texto vindo de vagas
  (contra XSS) e só aceita POST com Origin local (contra CSRF).
- As notificações no celular têm texto fixo, sem empresa, link ou dado pessoal.

## Operação

- **Rodada diária às 14h** via `systemd --user` timer com `Persistent=true` (se o PC estava desligado, roda ao
  ligar). A rodada só busca e filtra: não chama a API paga e não se candidata.
- **Botão PARAR:** um arquivo `PARAR` (pela CLI ou pelo painel) interrompe qualquer execução no próximo ponto
  seguro e impede que outra comece.
- **Sem duplicatas:** cada vaga tem uma chave (empresa + cargo + link normalizado). Uma vaga marcada como enviada
  não é preparada nem assistida de novo sem `--forcar`.
- **Encerramento limpo:** fechar a janela, Ctrl+C ou SIGTERM sempre gravam o resumo da sessão do copiloto.

## Stack

| Peça | Escolha | Por quê |
|---|---|---|
| Linguagem | Python 3.12 | ecossistema de automação e de LLM |
| Navegador | Playwright | preenchimento sem clique, perfil persistente, PDF do currículo |
| Banco | SQLite | local, sem servidor, suficiente para centenas de vagas |
| Modelo | SDK `anthropic` + pydantic | saída estruturada e validada |
| Painel | FastAPI + Jinja2, sem JavaScript | simples, seguro, sem build |
| Testes | pytest (sem rede, sem API, navegador headless contra páginas de teste locais) | rodam em qualquer máquina e no CI |

## Em aberto

- Nota de corte final do modelo (começa em 7/10; será calibrada depois de cerca de 30 vagas avaliadas).
- Palavras-chave de bloqueio e pontuação (`filtros.yaml`), a refinar com vagas reais.
- Reconhecimento de campos do copiloto, a ampliar com o log de depuração das candidaturas reais.
