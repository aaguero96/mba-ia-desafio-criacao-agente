"""Camada entre a API e o ADK: sessões, execução e confirmações.

Aqui moram duas coisas que o enunciado cobra de perto:

* **Garantia 2** — o apartamento é gravado no `state` da sessão em
  `criar_sessao` e nunca mais é tocado. Nenhuma rota aceita apartamento nas
  mensagens.
* **Garantia 1** — `responder_confirmacao` é o único lugar que produz o
  FunctionResponse `adk_request_confirmation` que o ADK aceita para retomar uma
  tool. Antes de produzi-lo, ele confere que o id está de fato pendente
  *nesta* sessão; qualquer outro id vira 409 e nada é executado.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from google.adk.runners import Runner
from google.adk.sessions.session import Session
from google.adk.sessions.sqlite_session_service import SqliteSessionService
from google.adk.tools.tool_confirmation import ToolConfirmation
from google.genai import types

from . import armazenamento, configuracao, dados
from .agentes.principal import app_aurora

# Nome da função que o ADK usa para pedir e receber confirmações de tools.
NOME_FUNCAO_CONFIRMACAO = "adk_request_confirmation"


class SessaoInexistente(Exception):
    """Não existe sessão com esse id (vira 404 na API)."""


class ConfirmacaoInvalida(Exception):
    """O id não corresponde a uma confirmação pendente nesta sessão (vira 409)."""


@dataclass(frozen=True)
class Pendencia:
    id: str
    acao: str
    detalhes: dict[str, Any]
    hint: str

    def para_api(self) -> dict[str, Any]:
        return {"id": self.id, "acao": self.acao, "detalhes": self.detalhes}


class ServicoAurora:
    def __init__(self) -> None:
        configuracao.garantir_diretorio_estado()
        armazenamento.preparar_banco()
        self._sessoes = SqliteSessionService(str(configuracao.BANCO_SESSOES))
        self._runner = Runner(app=app_aurora, session_service=self._sessoes)
        # Uma trava por sessão: duas sessões diferentes continuam correndo em
        # paralelo (é o que a Garantia 5 exige), mas a mesma sessão não executa
        # duas invocações ao mesmo tempo.
        self._travas: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    # ------------------------------------------------------------------
    # Sessões
    # ------------------------------------------------------------------

    async def criar_sessao(self, apartamento: str) -> str:
        sessao = await self._sessoes.create_session(
            app_name=configuracao.NOME_APP,
            user_id=configuracao.USUARIO_PADRAO,
            # O apartamento entra aqui, uma única vez. É a única origem do
            # apartamento usado pelas tools.
            state={configuracao.CHAVE_APARTAMENTO: apartamento},
        )
        return sessao.id

    async def _obter_sessao(self, session_id: str) -> Session:
        sessao = await self._sessoes.get_session(
            app_name=configuracao.NOME_APP,
            user_id=configuracao.USUARIO_PADRAO,
            session_id=session_id,
        )
        if sessao is None:
            raise SessaoInexistente(session_id)
        return sessao

    async def listar_eventos(self, session_id: str) -> list[dict[str, Any]]:
        sessao = await self._obter_sessao(session_id)
        return [evento.model_dump(mode="json", exclude_none=True) for evento in sessao.events]

    # ------------------------------------------------------------------
    # Conversa
    # ------------------------------------------------------------------

    async def enviar_mensagem(self, session_id: str, texto: str) -> dict[str, Any]:
        await self._obter_sessao(session_id)
        conteudo = types.Content(role="user", parts=[types.Part(text=texto)])
        async with self._travas[session_id]:
            return await self._executar(session_id, conteudo)

    async def responder_confirmacao(
        self, session_id: str, confirmacao_id: str, confirmado: bool
    ) -> dict[str, Any]:
        async with self._travas[session_id]:
            sessao = await self._obter_sessao(session_id)
            pendentes = {p.id: p for p in self._pendencias(sessao)}
            pendencia = pendentes.get(confirmacao_id)
            if pendencia is None:
                # Id desconhecido, id de outra sessão ou confirmação já
                # respondida: 409 e nada executa.
                raise ConfirmacaoInvalida(confirmacao_id)

            resposta_confirmacao = ToolConfirmation(
                hint=pendencia.hint,
                confirmed=confirmado,
                payload={"acao": pendencia.acao, "detalhes": pendencia.detalhes},
            )
            conteudo = types.Content(
                role="user",
                parts=[
                    types.Part(
                        function_response=types.FunctionResponse(
                            id=confirmacao_id,
                            name=NOME_FUNCAO_CONFIRMACAO,
                            response=resposta_confirmacao.model_dump(by_alias=True),
                        )
                    )
                ],
            )
            return await self._executar(session_id, conteudo)

    async def _executar(self, session_id: str, conteudo: types.Content) -> dict[str, Any]:
        partes_texto: list[str] = []
        async for evento in self._runner.run_async(
            user_id=configuracao.USUARIO_PADRAO,
            session_id=session_id,
            new_message=conteudo,
        ):
            if evento.author == "user" or not evento.content or not evento.content.parts:
                continue
            for parte in evento.content.parts:
                if parte.text:
                    partes_texto.append(parte.text.strip())

        sessao = await self._obter_sessao(session_id)
        return {
            "resposta": "\n".join(t for t in partes_texto if t),
            "confirmacoes_pendentes": [p.para_api() for p in self._pendencias(sessao)],
        }

    # ------------------------------------------------------------------
    # Confirmações pendentes
    # ------------------------------------------------------------------

    @staticmethod
    def _pendencias(sessao: Session) -> list[Pendencia]:
        """Lê os eventos da sessão e devolve as confirmações ainda em aberto.

        Um pedido de confirmação é uma function call `adk_request_confirmation`
        emitida pelo agente. Ele deixa de estar pendente quando existe um
        FunctionResponse com o mesmo id — que só a rota de confirmações produz.
        """
        pedidos: dict[str, Pendencia] = {}
        respondidos: set[str] = set()

        for evento in sessao.events:
            for chamada in evento.get_function_calls():
                if chamada.name != NOME_FUNCAO_CONFIRMACAO or not chamada.id:
                    continue
                confirmacao = (chamada.args or {}).get("toolConfirmation") or {}
                carga = confirmacao.get("payload") or {}
                pedidos[chamada.id] = Pendencia(
                    id=chamada.id,
                    acao=str(carga.get("acao", "acao_desconhecida")),
                    detalhes=dict(carga.get("detalhes") or {}),
                    hint=str(confirmacao.get("hint", "")),
                )
            for resposta in evento.get_function_responses():
                if resposta.name == NOME_FUNCAO_CONFIRMACAO and resposta.id:
                    respondidos.add(resposta.id)

        return [p for id_, p in pedidos.items() if id_ not in respondidos]

    # ------------------------------------------------------------------
    # Rotas de verificação: leem o banco direto, sem passar pelo modelo
    # ------------------------------------------------------------------

    @staticmethod
    async def reservas_do_apartamento(apartamento: str) -> list[dict[str, str]]:
        reservas = await armazenamento.listar_reservas(apartamento)
        return [r.para_api() for r in reservas]

    @staticmethod
    async def visitantes_do_apartamento(apartamento: str) -> list[dict[str, str]]:
        return await armazenamento.listar_visitantes(apartamento)

    @staticmethod
    def apartamento_existe(numero: str) -> bool:
        return dados.apartamento_existe(numero)

    async def encerrar(self) -> None:
        await self._runner.close()
