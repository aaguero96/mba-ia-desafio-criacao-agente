"""A API do Residencial Aurora (contrato do enunciado).

Rotas de conversa:
    POST /sessoes
    POST /sessoes/{session_id}/mensagens
    POST /sessoes/{session_id}/confirmacoes
    GET  /sessoes/{session_id}/eventos

Rotas de verificação (leem o banco direto, sem passar pelo modelo):
    GET /apartamentos/{numero}/reservas
    GET /apartamentos/{numero}/visitantes
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .servico import ConfirmacaoInvalida, SessaoInexistente, ServicoAurora

servico: ServicoAurora | None = None


def _servico() -> ServicoAurora:
    if servico is None:  # pragma: no cover - só acontece fora do ciclo do app
        raise RuntimeError("Serviço não inicializado.")
    return servico


@asynccontextmanager
async def ciclo_de_vida(_: FastAPI):
    global servico
    servico = ServicoAurora()
    try:
        yield
    finally:
        await servico.encerrar()
        servico = None


api = FastAPI(
    title="Assistente do Residencial Aurora",
    version="1.0.0",
    lifespan=ciclo_de_vida,
)


class NovaSessao(BaseModel):
    apartamento: str = Field(min_length=1)


class NovaMensagem(BaseModel):
    texto: str = Field(min_length=1)


class RespostaConfirmacao(BaseModel):
    id: str = Field(min_length=1)
    confirmado: bool


@api.exception_handler(SessaoInexistente)
async def _sessao_inexistente(_, __) -> JSONResponse:
    return JSONResponse(status_code=404, content={"detail": "Sessão não encontrada."})


@api.exception_handler(ConfirmacaoInvalida)
async def _confirmacao_invalida(_, __) -> JSONResponse:
    return JSONResponse(
        status_code=409,
        content={"detail": "Não existe confirmação pendente com esse id nesta sessão."},
    )


@api.post("/sessoes", status_code=201)
async def criar_sessao(corpo: NovaSessao) -> dict[str, str]:
    session_id = await _servico().criar_sessao(corpo.apartamento)
    return {"session_id": session_id}


@api.post("/sessoes/{session_id}/mensagens")
async def enviar_mensagem(session_id: str, corpo: NovaMensagem) -> dict[str, Any]:
    return await _servico().enviar_mensagem(session_id, corpo.texto)


@api.post("/sessoes/{session_id}/confirmacoes")
async def responder_confirmacao(
    session_id: str, corpo: RespostaConfirmacao
) -> dict[str, Any]:
    return await _servico().responder_confirmacao(session_id, corpo.id, corpo.confirmado)


@api.get("/sessoes/{session_id}/eventos")
async def listar_eventos(session_id: str) -> list[dict[str, Any]]:
    return await _servico().listar_eventos(session_id)


@api.get("/apartamentos/{numero}/reservas")
async def reservas_do_apartamento(numero: str) -> list[dict[str, str]]:
    return await _servico().reservas_do_apartamento(numero)


@api.get("/apartamentos/{numero}/visitantes")
async def visitantes_do_apartamento(numero: str) -> list[dict[str, str]]:
    return await _servico().visitantes_do_apartamento(numero)


@api.get("/saude")
async def saude() -> dict[str, str]:
    return {"status": "ok"}


def main() -> None:
    """Sobe a API em http://localhost:8000 (comando `aurora-api`)."""
    import uvicorn

    uvicorn.run("app.api:api", host="0.0.0.0", port=8000, reload=False)


if __name__ == "__main__":  # pragma: no cover
    main()
