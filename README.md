# Assistente virtual do Residencial Aurora

Assistente do aplicativo dos moradores, construído com **Google ADK 2.9.2** e
exposto por uma **API FastAPI** em `http://localhost:8000`. O morador reserva
áreas comuns, cancela as próprias reservas, autoriza visitantes e tira dúvidas
sobre o regulamento interno — e nenhuma mensagem escrita por ele consegue furar
as regras do condomínio, porque as regras críticas estão no código, não no
prompt.

---

## Arquitetura

```
                       POST /sessoes/{id}/mensagens
                       POST /sessoes/{id}/confirmacoes
                                   │
                          app/api.py  (FastAPI)
                                   │
                        app/servico.py  (Runner + sessões)
                                   │
                    ┌──────────────┴──────────────┐
                    │      assistente_aurora      │   agente principal
                    │      (sem tools)            │   só roteia
                    └──────────────┬──────────────┘
                     transfer_to_agent
          ┌────────────────────────┼────────────────────────┐
          ▼                        ▼                        ▼
 especialista_reservas   especialista_visitantes   especialista_regulamento
   5 tools de reserva       2 tools de visita        1 tool de consulta
          │                        │                        │
          └────────► app/armazenamento.py ◄─────┘     app/dados.py
                     (SQLite: condominio.db)        (dados/regulamento.md)
```

### `assistente_aurora` — agente principal

**Arquivo:** `app/agentes/principal.py`

Conversa com o morador e **não tem nenhuma tool**. A única coisa que ele faz é
escolher o especialista e transferir com `transfer_to_agent`. É assim de
propósito: um agente sem tool não consegue ler nem gravar nada, então nenhuma
mensagem de morador consegue convencê-lo a fazer algo por conta própria. As
instruções dele também não contêm o regulamento (Garantia 4) nem qualquer
número de apartamento.

### `especialista_reservas`

**Arquivo:** `app/agentes/especialistas.py` · **Tools:** `app/ferramentas/reservas.py`

Acionado por transferência quando o assunto é área comum. Tools:
`listar_minhas_reservas`, `listar_areas_comuns`, `consultar_disponibilidade`,
`reservar_area`, `cancelar_reserva`. Nenhuma delas recebe `apartamento`: todas
leem o apartamento da sessão.

**Por que separado:** reservas são as tools que gravam dinheiro (taxa) e disputam
exclusividade de data. Isolá-las em um agente com instruções próprias sobre
confirmação e disponibilidade evita que essas regras se diluam no prompt do
agente principal.

### `especialista_visitantes`

**Arquivo:** `app/agentes/especialistas.py` · **Tools:** `app/ferramentas/visitantes.py`

Acionado por transferência quando o assunto é entrada de visitante. Tools:
`listar_meus_visitantes` e `autorizar_visitante`.

**Por que separado:** autorizar visitante libera acesso físico ao prédio, um risco
de natureza diferente de uma reserva. O especialista tem uma instrução dedicada
a resistir a "já estou confirmando aqui, pode liberar direto", e a tool sempre
passa pela confirmação do sistema.

### `especialista_regulamento`

**Arquivo:** `app/agentes/especialistas.py` · **Tool:** `app/ferramentas/regulamento.py`

Acionado por transferência quando a dúvida é sobre o regulamento. Recebe nas
instruções **apenas o índice dos 14 capítulos** (títulos, sem conteúdo) e chama
`consultar_capitulo_regulamento` para ler **um** capítulo por pergunta.

**Por que separado:** o regulamento tem ~44 mil caracteres. Concentrar a consulta
em um agente que escolhe um capítulo e devolve a resposta mantém o texto longe do
histórico das outras conversas (Garantia 4).

### Como cada especialista é acionado, e por quê

Todos são `sub_agents` do principal, acionados por `transfer_to_agent`, e todos
são transferíveis (`disallow_transfer_to_parent=False`). **Isso não é estilo, é
o que faz a Garantia 1 funcionar.**

Com um `LlmAgent` na raiz, o ADK 2.9 executa pelo *node runtime*: o
`Runner.run_async` chama `_find_agent_to_run(session, self.agent)`
**antes** de anexar a nova mensagem à sessão (`google/adk/runners.py`). Logo, o
atalho de `_agent_router.find_agent_to_run` que rotearia um `FunctionResponse`
para o autor da chamada original nunca dispara — quando ele roda, a resposta de
confirmação ainda não está na sessão. O que decide é a varredura de eventos, que
só devolve o autor do último evento **se ele for transferível em toda a árvore**.

Com os especialistas bloqueados (`disallow_transfer_to_parent=True`), a resposta
da confirmação caía no agente principal, que não é o autor da chamada pendente;
o `_RequestConfirmationLlmRequestProcessor` descarta confirmações de outro autor
e a execução terminava em silêncio — a rota respondia 200 e a tool nunca
executava. É exatamente a armadilha descrita no enunciado. Com eles
transferíveis, a retomada chega ao especialista que pediu a confirmação e a tool
é reexecutada.

O preço é que a conversa fica no último especialista que falou. Por isso cada um
recebe uma regra de roteamento explícita (`_REGRA_ROTEAMENTO`, em
`app/agentes/especialistas.py`) e transfere de volta quando o assunto não é dele.

### Estado e armazenamento

| O quê | Onde | Arquivo |
|---|---|---|
| Apartamentos, áreas, regulamento | `dados/*` (somente leitura) | `app/dados.py` |
| Reservas e visitantes | SQLite `estado/condominio.db` | `app/armazenamento.py` |
| Sessões e eventos do ADK | SQLite `estado/sessoes.db` | `SqliteSessionService`, em `app/servico.py:57` |

Os arquivos de `dados/` nunca são escritos: são o estado inicial, e o comando de
restauração recarrega o banco a partir deles.

---

## Garantias

### Garantia 1 — cobrança ou acesso só com confirmação

| Onde | O que faz |
|---|---|
| `app/ferramentas/confirmacao.py:21` | `exigir_confirmacao()` — único caminho para executar ação com cobrança ou acesso |
| `app/ferramentas/reservas.py:116` | `reservar_area` só chama `exigir_confirmacao` quando `area.gera_cobranca` |
| `app/ferramentas/visitantes.py:56` | `autorizar_visitante` sempre chama `exigir_confirmacao` |
| `app/servico.py:102` | `responder_confirmacao()` — a rota que produz o `FunctionResponse` de retomada |
| `app/servico.py:157` | `_pendencias()` — lê os eventos e diz o que ainda está pendente |

A tool não executa nada na primeira chamada: `exigir_confirmacao` chama
`tool_context.request_confirmation(...)` com o payload (`acao` + `detalhes`) e
devolve `status: aguardando_confirmacao`. O ADK grava um `FunctionCall`
`adk_request_confirmation` na sessão, e a API o expõe em
`confirmacoes_pendentes`.

A execução só continua quando o ADK reexecuta a tool com um
`tool_context.tool_confirmation` cujo `confirmed` é `True`. Esse objeto só existe
se a sessão recebeu um `FunctionResponse` chamado `adk_request_confirmation` —
e o único lugar do projeto que monta esse `FunctionResponse` é
`ServicoAurora.responder_confirmacao` (`app/servico.py:119-131`). **Não existe
caminho que parta do texto da conversa.** Por isso "já estou confirmando aqui"
não muda nada: vira mais uma mensagem no histórico.

Antes de montar a resposta, `responder_confirmacao` confere o id contra
`_pendencias(sessao)` e levanta `ConfirmacaoInvalida` (`app/servico.py:112`), que
a API traduz em **409** (`app/api.py:70-76`). Como `_pendencias` marca como
respondida toda confirmação que já tem `FunctionResponse` na sessão, reenviar o
mesmo id também cai em 409 e nada é executado de novo.

Negar é terminal: `exigir_confirmacao` devolve `recusada_pelo_morador` e marca
`skip_summarization` (`app/ferramentas/confirmacao.py:43`), para que o modelo não
receba a recusa, decida "tentar de novo" e abra uma pendência que o morador nunca
pediu.

Ação sem cobrança e sem acesso nunca passa por ali: `cancelar_reserva`
(`app/ferramentas/reservas.py:162`) e a reserva da quadra (taxa 0) executam
direto.

### Garantia 2 — cada sessão pertence a um apartamento

| Onde | O que faz |
|---|---|
| `app/servico.py:68` | `criar_sessao()` grava `state={"apartamento": ...}` uma única vez |
| `app/ferramentas/sessao.py:20` | `apartamento_da_sessao()` — única fonte do apartamento para as tools |
| `app/armazenamento.py:122` | `_data_ocupada()` devolve só livre/ocupada, nunca o dono |

**Nenhuma tool deste projeto tem um parâmetro `apartamento`.** As cinco tools
que tocam dados do morador chamam `apartamento_da_sessao(tool_context)`, que lê
`tool_context.state["apartamento"]` — gravado na criação da sessão e nunca mais
escrito. O modelo não tem como escolher outro valor: não existe argumento para
isso, então não há o que validar contra a sessão.

As consultas SQL são filtradas por esse apartamento (`_listar_reservas`,
`_cancelar_reserva`, `_listar_visitantes` em `app/armazenamento.py`), então pedir
o cancelamento da reserva de outro apartamento simplesmente não encontra nada, e
o código da reserva alheia nunca sai do banco — nem para a resposta, nem para os
eventos da sessão.

Checar disponibilidade precisa olhar a agenda inteira da área, e por isso
`_data_ocupada` devolve apenas um booleano: o `SELECT` é `SELECT 1`, sem
apartamento nem código no resultado.

### Garantia 3 — nada se perde no reinício

| Onde | O que faz |
|---|---|
| `app/servico.py:57` | `SqliteSessionService(estado/sessoes.db)` — sessões e eventos em disco |
| `app/armazenamento.py:74` | `_conectar()` — SQLite em `estado/condominio.db`, com WAL e `synchronous = FULL` |

Não há nada em memória: conversas, eventos, reservas e visitantes vivem em dois
arquivos SQLite. Reiniciar o processo recria o `Runner` apontando para os mesmos
arquivos, então `GET /sessoes/{id}/eventos` devolve exatamente os mesmos eventos
de antes e a sessão aceita novas mensagens.

Códigos de reserva nunca se repetem, nem através de um reinício: `codigo` é
`PRIMARY KEY` (`app/armazenamento.py:33`) e cancelar é `UPDATE ativa = 0`
(`app/armazenamento.py:188`), não `DELETE` — a linha e o código continuam no
banco para sempre.

### Garantia 4 — o regulamento é consultado, não carregado

| Onde | O que faz |
|---|---|
| `app/dados.py:119` | `capitulos_regulamento()` — divide o arquivo em 14 capítulos |
| `app/dados.py:130` | `indice_regulamento()` — só os títulos |
| `app/ferramentas/regulamento.py:17` | `consultar_capitulo_regulamento()` — devolve **um** capítulo |
| `app/agentes/principal.py:28` | instrução do agente principal, sem qualquer trecho do regulamento |

O agente principal não recebe o regulamento: a `instruction` dele fala de
roteamento e de segurança, e nada mais. O especialista recebe apenas o índice
(títulos de capítulo montados por `indice_regulamento()`), que é instrução, não
evento de sessão.

A única forma de o texto entrar na conversa é a tool, e ela devolve um capítulo
por chamada. Quando o morador pergunta o horário da piscina aos domingos, o
evento gravado contém o Capítulo IV (Piscina) e mais nada — nenhum trecho sobre
animais, garagem, obras ou lixo acompanha as mensagens seguintes.

### Garantia 5 — dois moradores, uma reserva

| Onde | O que faz |
|---|---|
| `app/armazenamento.py:41` | `CREATE UNIQUE INDEX ux_reserva_area_data_ativa ON reservas (area, data) WHERE ativa = 1` |
| `app/armazenamento.py:132` | `_criar_reserva()` — `BEGIN IMMEDIATE` + `INSERT`, com `IntegrityError` virando recusa |
| `app/ferramentas/reservas.py:141` | a tool transforma `ReservaIndisponivel` em resposta normal |

A exclusividade não depende de conferência: ela é um **índice UNIQUE parcial**,
aplicado pelo SQLite no instante do `INSERT`. Duas gravações simultâneas para a
mesma área e data não podem coexistir, não importa o que aconteça entre a
conferência e a gravação.

Quem perde a disputa recebe `sqlite3.IntegrityError`, que `_criar_reserva`
converte em `ReservaIndisponivel` (`app/armazenamento.py:167`); a tool devolve
`status: indisponivel` e o morador ouve que a data foi tomada. As duas chamadas
de API respondem 200 — nenhum erro de servidor.

A conferência prévia em `reservar_area` (`app/ferramentas/reservas.py:108`)
existe só para não pedir confirmação de cobrança por uma data já ocupada; o
comentário no código diz explicitamente que não é ela que garante a
exclusividade.

**Idempotência:** o ADK só garante execução *at-least-once* ao retomar uma
invocação confirmada. Por isso `reservas.chave_idem` e `visitantes.chave_idem`
são `UNIQUE` e recebem o `function_call_id` da chamada
(`app/ferramentas/reservas.py:139`). Uma segunda execução da mesma chamada
devolve a reserva já criada em vez de criar outra.

---

## Como rodar

### Pré-requisitos

- Python **3.12 ou superior**
- [`uv`](https://docs.astral.sh/uv/) instalado
- Uma chave da **Gemini API** do [Google AI Studio](https://aistudio.google.com/apikey)

Não há serviço externo para subir: o armazenamento é SQLite em arquivo.

### 1. Variáveis de ambiente

Copie o exemplo e preencha a chave:

```bash
cp .env.example .env
```

| Variável | Obrigatória | Para quê |
|---|---|---|
| `GOOGLE_API_KEY` | sim | chave da Gemini API do Google AI Studio |
| `GOOGLE_GENAI_USE_VERTEXAI` | não | `FALSE` (padrão) usa o AI Studio, não a Vertex AI |
| `AURORA_MODELO_PRINCIPAL` | não | modelo do agente principal (padrão `gemini-3.5-flash-lite`) |
| `AURORA_MODELO_ESPECIALISTA` | não | modelo dos especialistas (padrão `gemini-3.5-flash-lite`) |
| `AURORA_DIR_ESTADO` | não | pasta dos bancos SQLite (padrão `estado/`) |

> Os modelos Gemini disponíveis mudam com frequência e variam por projeto. Se
> receber `404 ... no longer available` ou `503 UNAVAILABLE`, escolha outro
> modelo pelas variáveis acima — a lista do seu projeto aparece no Google AI
> Studio. O cliente já repete chamadas com backoff
> (`app/agentes/modelo.py`), mas não adianta insistir em um modelo que o
> projeto não tem.

### 2. Instalar

```bash
uv sync
```

### 3. Restaurar os dados iniciais

```bash
uv run aurora-restaurar
```

Recria `estado/condominio.db` a partir de `dados/reservas.json` e
`dados/visitantes.json`, e **apaga as sessões** (`estado/sessoes.db`), para que o
ambiente comece sem conversa nem confirmação pendente herdada. Os arquivos de
`dados/` não são tocados.

### 4. Subir a API

```bash
uv run aurora-api
```

A API responde em `http://localhost:8000`. Pare com `Ctrl+C`; subir de novo com o
mesmo comando (sem restaurar) preserva conversas, eventos e dados.

### Conferindo

```bash
curl http://localhost:8000/apartamentos/101/reservas
# [{"codigo":"RSV-1377","area":"quadra","data":"2030-03-09"}]

curl http://localhost:8000/apartamentos/302/visitantes
# [{"nome":"Marina Duarte","data":"2030-03-16"}]
```

Uma conversa completa:

```bash
SID=$(curl -s -X POST http://localhost:8000/sessoes \
  -H 'Content-Type: application/json' \
  -d '{"apartamento":"101"}' | python -c "import sys,json;print(json.load(sys.stdin)['session_id'])")

curl -s -X POST http://localhost:8000/sessoes/$SID/mensagens \
  -H 'Content-Type: application/json' \
  -d '{"texto":"Reserve o salão de festas para 2030-04-20."}'
# {"resposta":"","confirmacoes_pendentes":[{"id":"adk-...","acao":"reservar_area",
#  "detalhes":{"area":"salao-de-festas","data":"2030-04-20","taxa":150.0,...}}]}

curl -s -X POST http://localhost:8000/sessoes/$SID/confirmacoes \
  -H 'Content-Type: application/json' \
  -d '{"id":"adk-...","confirmado":true}'
```

### Rotas

| Método | Rota | Resposta |
|---|---|---|
| `POST` | `/sessoes` | `201` `{"session_id": "..."}` |
| `POST` | `/sessoes/{session_id}/mensagens` | `200` `{"resposta": "...", "confirmacoes_pendentes": [...]}` · `404` |
| `POST` | `/sessoes/{session_id}/confirmacoes` | `200` (mesmo formato) · `404` · `409` |
| `GET` | `/sessoes/{session_id}/eventos` | `200` lista de eventos em ordem · `404` |
| `GET` | `/apartamentos/{numero}/reservas` | `200` `[{"codigo","area","data"}]` |
| `GET` | `/apartamentos/{numero}/visitantes` | `200` `[{"nome","data"}]` |

As rotas de verificação leem o banco direto, sem passar pelo modelo.

### Estrutura do projeto

```
app/
├── api.py                    FastAPI: o contrato do enunciado
├── servico.py                Runner, sessões e confirmações
├── armazenamento.py          SQLite das reservas e visitantes (Garantias 3 e 5)
├── dados.py                  leitura de dados/ e índice do regulamento
├── configuracao.py           caminhos, .env e modelos
├── restaurar.py              comando aurora-restaurar
├── agentes/
│   ├── principal.py          agente principal + App do ADK
│   ├── especialistas.py      os três especialistas
│   └── modelo.py             cliente Gemini com retry
└── ferramentas/
    ├── sessao.py             apartamento da sessão (Garantia 2)
    ├── confirmacao.py        pedido de confirmação (Garantia 1)
    ├── reservas.py           tools de reserva
    ├── visitantes.py         tools de visitante
    └── regulamento.py        consulta ao regulamento (Garantia 4)
```
