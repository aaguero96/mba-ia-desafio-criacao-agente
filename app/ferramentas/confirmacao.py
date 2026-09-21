"""Garantia 1: cobrança ou acesso só com confirmação.

`exigir_confirmacao` é o único caminho por onde uma tool que gera cobrança ou
libera acesso pode executar. A primeira chamada registra a pendência no ADK
(`request_confirmation`) e devolve um resultado "aguardando"; a tool só passa
adiante quando o ADK reexecuta a chamada trazendo um `tool_confirmation` com
`confirmed = True`.

Esse `tool_confirmation` só existe se o ADK recebeu um FunctionResponse
`adk_request_confirmation` — que, nesta API, só é produzido pela rota
`POST /sessoes/{id}/confirmacoes`. Por isso o morador escrever "já estou
confirmando aqui" não muda nada: o texto vira mais uma mensagem da conversa e a
ação continua pendente.
"""

from __future__ import annotations

from google.adk.tools.tool_context import ToolContext


def exigir_confirmacao(
    tool_context: ToolContext, *, acao: str, hint: str, detalhes: dict
) -> dict | None:
    """Trava a execução até a rota de confirmações responder.

    Returns:
        O dicionário que a tool deve devolver enquanto a ação está pendente ou
        foi negada, ou `None` quando a execução pode seguir.
    """
    confirmacao = tool_context.tool_confirmation
    if confirmacao is None:
        tool_context.request_confirmation(
            hint=hint,
            payload={"acao": acao, "detalhes": detalhes},
        )
        # Sem resumo do modelo: a execução para aqui e a API devolve a pendência.
        tool_context.actions.skip_summarization = True
        return {"status": "aguardando_confirmacao", "detalhes": detalhes}
    if not confirmacao.confirmed:
        # Negar encerra a ação. Sem `skip_summarization`, o modelo recebe a
        # recusa, decide "tentar de novo" e abre uma nova pendência que o
        # morador nunca pediu — uma negativa não pode rearmar a cobrança.
        tool_context.actions.skip_summarization = True
        return {"status": "recusada_pelo_morador", "detalhes": detalhes}
    return None
