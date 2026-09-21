"""Tool de consulta ao regulamento interno (Garantia 4).

O regulamento tem 14 capítulos e cerca de 44 mil caracteres. A tool nunca
devolve o documento inteiro: devolve **um** capítulo, o que o especialista
escolheu a partir do índice que está na instrução dele. Assim o evento que
entra no histórico da sessão carrega só o capítulo que responde a pergunta, e
nenhum trecho de capítulo sobre outro assunto acompanha as mensagens seguintes.
"""

from __future__ import annotations

from google.adk.tools.tool_context import ToolContext

from .. import dados


async def consultar_capitulo_regulamento(capitulo: str, tool_context: ToolContext) -> dict:
    """Devolve o texto de UM capítulo do regulamento interno.

    Escolha o capítulo pelo índice que está nas suas instruções e peça apenas
    ele. Nunca peça vários capítulos para a mesma pergunta.

    Args:
        capitulo: título do capítulo, por exemplo "Capítulo IV: Piscina".

    Returns:
        Um dicionário com `titulo` e `texto` do capítulo, ou um erro com a lista
        de títulos disponíveis quando o capítulo não é reconhecido.
    """
    encontrado = dados.buscar_capitulo(capitulo)
    if encontrado is None:
        return {
            "erro": f"Não encontrei o capítulo '{capitulo}'.",
            "capitulos_disponiveis": dados.indice_regulamento(),
        }
    return {"titulo": encontrado.titulo, "texto": encontrado.texto}
