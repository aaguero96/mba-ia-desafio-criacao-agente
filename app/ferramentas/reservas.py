"""Tools de reserva de área comum.

Tudo que o assistente sabe sobre reservas passa por aqui: ele nunca responde de
memória. As quatro regras que o código impõe, independentemente do prompt:

1. o apartamento vem da sessão (Garantia 2);
2. reservar área com taxa fica pendente de confirmação (Garantia 1);
3. a exclusividade área+data é decidida pelo banco no INSERT (Garantia 5);
4. a disponibilidade devolve apenas livre/ocupada, nunca o dono da reserva.
"""

from __future__ import annotations

from google.adk.tools.tool_context import ToolContext

from .. import armazenamento, dados
from .confirmacao import exigir_confirmacao
from .sessao import apartamento_da_sessao

ACAO_RESERVAR = "reservar_area"


async def listar_minhas_reservas(tool_context: ToolContext) -> dict:
    """Lista as reservas ativas do apartamento da sessão.

    Use sempre que o morador perguntar quais são as reservas dele. Não existe
    forma de listar as reservas de outro apartamento.

    Returns:
        Um dicionário com a lista `reservas`, cada item com codigo, area e data.
    """
    apartamento = apartamento_da_sessao(tool_context)
    reservas = await armazenamento.listar_reservas(apartamento)
    return {
        "apartamento": apartamento,
        "reservas": [r.para_api() for r in reservas],
    }


async def listar_areas_comuns(tool_context: ToolContext) -> dict:
    """Lista as áreas comuns do condomínio com o id, o nome e a taxa em reais.

    Returns:
        Um dicionário com a lista `areas`. Taxa 0 significa área sem cobrança.
    """
    return {
        "areas": [
            {"id": a.id, "nome": a.nome, "taxa": a.taxa, "gera_cobranca": a.gera_cobranca}
            for a in dados.areas().values()
        ]
    }


async def consultar_disponibilidade(area: str, data: str, tool_context: ToolContext) -> dict:
    """Informa se uma área comum está livre em uma data.

    Args:
        area: id ou nome da área comum, por exemplo "salao-de-festas".
        data: data no formato AAAA-MM-DD.

    Returns:
        Um dicionário com `disponivel`. Quando a data está ocupada, a resposta
        não diz de quem é a reserva: essa informação não sai do banco.
    """
    area_resolvida = dados.resolver_area(area)
    if area_resolvida is None:
        return {"erro": f"Não existe área comum chamada '{area}'."}
    if not dados.data_valida(data):
        return {"erro": "Data inválida. Use o formato AAAA-MM-DD."}

    ocupada = await armazenamento.data_ocupada(area_resolvida.id, data)
    return {
        "area": area_resolvida.id,
        "nome_area": area_resolvida.nome,
        "data": data,
        "disponivel": not ocupada,
        "taxa": area_resolvida.taxa,
        "gera_cobranca": area_resolvida.gera_cobranca,
    }


async def reservar_area(area: str, data: str, tool_context: ToolContext) -> dict:
    """Reserva uma área comum para o apartamento da sessão.

    Reservar área com taxa maior que zero gera cobrança e por isso fica pendente
    de confirmação do morador; a confirmação chega pela rota de confirmações da
    API, nunca pelo texto da conversa.

    Args:
        area: id ou nome da área comum, por exemplo "salao-de-festas".
        data: data no formato AAAA-MM-DD.

    Returns:
        Um dicionário com o `status` da operação e, quando a reserva é criada,
        o `codigo` gerado pelo sistema.
    """
    apartamento = apartamento_da_sessao(tool_context)

    area_resolvida = dados.resolver_area(area)
    if area_resolvida is None:
        return {"status": "erro", "motivo": f"Não existe área comum chamada '{area}'."}
    if not dados.data_valida(data):
        return {"status": "erro", "motivo": "Data inválida. Use o formato AAAA-MM-DD."}

    # Conferência antecipada: evita pedir confirmação de cobrança por uma data
    # que já está ocupada. NÃO é o que garante a exclusividade — quem garante é
    # o índice UNIQUE parcial no INSERT (ver app/armazenamento.py).
    if await armazenamento.data_ocupada(area_resolvida.id, data):
        return {
            "status": "indisponivel",
            "area": area_resolvida.id,
            "data": data,
            "motivo": "A área já tem reserva nessa data.",
        }

    if area_resolvida.gera_cobranca:
        pendente = exigir_confirmacao(
            tool_context,
            acao=ACAO_RESERVAR,
            hint=(
                f"Reservar {area_resolvida.nome} em {data} gera cobrança de "
                f"R$ {area_resolvida.taxa:.2f} para o apartamento {apartamento}."
            ),
            detalhes={
                "area": area_resolvida.id,
                "nome_area": area_resolvida.nome,
                "data": data,
                "taxa": area_resolvida.taxa,
            },
        )
        if pendente is not None:
            return pendente

    try:
        reserva = await armazenamento.criar_reserva(
            apartamento,
            area_resolvida.id,
            data,
            chave_idem=f"reserva:{tool_context.function_call_id}",
        )
    except armazenamento.ReservaIndisponivel:
        # Alguém gravou a mesma área e data entre a conferência e o INSERT.
        # Recusa de negócio normal, sem erro de servidor.
        return {
            "status": "indisponivel",
            "area": area_resolvida.id,
            "data": data,
            "motivo": "A área foi reservada por outro morador nesse instante.",
        }

    return {
        "status": "reservado",
        "codigo": reserva.codigo,
        "area": reserva.area,
        "nome_area": area_resolvida.nome,
        "data": reserva.data,
        "gerou_cobranca": area_resolvida.gera_cobranca,
        "taxa": area_resolvida.taxa,
    }


async def cancelar_reserva(area: str, data: str, tool_context: ToolContext) -> dict:
    """Cancela uma reserva do apartamento da sessão, sem confirmação.

    Só encontra reservas do próprio apartamento: pedir o cancelamento da reserva
    de outro apartamento simplesmente não acha nada.

    Args:
        area: id ou nome da área comum, por exemplo "salao-de-festas".
        data: data no formato AAAA-MM-DD.

    Returns:
        Um dicionário com o `status` e, quando cancela, o `codigo` da reserva.
    """
    apartamento = apartamento_da_sessao(tool_context)

    area_resolvida = dados.resolver_area(area)
    if area_resolvida is None:
        return {"status": "erro", "motivo": f"Não existe área comum chamada '{area}'."}
    if not dados.data_valida(data):
        return {"status": "erro", "motivo": "Data inválida. Use o formato AAAA-MM-DD."}

    reserva = await armazenamento.cancelar_reserva(apartamento, area_resolvida.id, data)
    if reserva is None:
        return {
            "status": "nao_encontrada",
            "area": area_resolvida.id,
            "data": data,
            "motivo": "O apartamento desta sessão não tem reserva dessa área nessa data.",
        }
    return {
        "status": "cancelada",
        "codigo": reserva.codigo,
        "area": reserva.area,
        "data": reserva.data,
    }
