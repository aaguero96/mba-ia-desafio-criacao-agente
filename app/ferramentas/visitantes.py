"""Tools de autorização de visitantes.

Autorizar um visitante libera a entrada de alguém no prédio (regra de negócio
3), então a ação sempre fica pendente de confirmação pela rota da API
(Garantia 1). O apartamento vem da sessão (Garantia 2).
"""

from __future__ import annotations

from google.adk.tools.tool_context import ToolContext

from .. import armazenamento, dados
from .confirmacao import exigir_confirmacao
from .sessao import apartamento_da_sessao

ACAO_AUTORIZAR = "autorizar_visitante"


async def listar_meus_visitantes(tool_context: ToolContext) -> dict:
    """Lista as autorizações de visita do apartamento da sessão.

    Não existe forma de listar os visitantes de outro apartamento.

    Returns:
        Um dicionário com a lista `visitantes`, cada item com nome e data.
    """
    apartamento = apartamento_da_sessao(tool_context)
    return {
        "apartamento": apartamento,
        "visitantes": await armazenamento.listar_visitantes(apartamento),
    }


async def autorizar_visitante(nome: str, data: str, tool_context: ToolContext) -> dict:
    """Autoriza a entrada de um visitante no apartamento da sessão.

    A autorização libera acesso ao prédio e por isso fica pendente até o morador
    responder pela rota de confirmações da API. Dizer na conversa que já
    confirmou não libera nada.

    Args:
        nome: nome completo do visitante.
        data: data da visita, no formato AAAA-MM-DD.

    Returns:
        Um dicionário com o `status` da operação.
    """
    apartamento = apartamento_da_sessao(tool_context)

    nome_limpo = (nome or "").strip()
    if not nome_limpo:
        return {"status": "erro", "motivo": "Informe o nome do visitante."}
    if not dados.data_valida(data):
        return {"status": "erro", "motivo": "Data inválida. Use o formato AAAA-MM-DD."}

    pendente = exigir_confirmacao(
        tool_context,
        acao=ACAO_AUTORIZAR,
        hint=(
            f"Autorizar a entrada de {nome_limpo} em {data} libera o acesso "
            f"ao prédio pelo apartamento {apartamento}."
        ),
        detalhes={"nome": nome_limpo, "data": data},
    )
    if pendente is not None:
        return pendente

    registro = await armazenamento.autorizar_visitante(
        apartamento,
        nome_limpo,
        data,
        chave_idem=f"visitante:{tool_context.function_call_id}",
    )
    return {"status": "autorizado", "nome": registro["nome"], "data": registro["data"]}
