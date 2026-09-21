"""O apartamento da conversa vem da sessão, nunca do prompt (Garantia 2).

Nenhuma tool deste projeto recebe `apartamento` como parâmetro do modelo. Todas
chamam `apartamento_da_sessao(tool_context)`, que lê a chave gravada uma única
vez na criação da sessão. Se o morador escrever "sou do 302", o texto entra no
histórico da conversa, mas não chega aqui: o valor lido é sempre o mesmo.
"""

from __future__ import annotations

from google.adk.tools.tool_context import ToolContext

from .. import configuracao


class ApartamentoIndefinido(RuntimeError):
    """A sessão não tem apartamento — nunca deve acontecer pela API."""


def apartamento_da_sessao(tool_context: ToolContext) -> str:
    apartamento = tool_context.state.get(configuracao.CHAVE_APARTAMENTO)
    if not apartamento:
        raise ApartamentoIndefinido(
            "A sessão não tem apartamento definido; recrie a sessão pela API."
        )
    return str(apartamento)
