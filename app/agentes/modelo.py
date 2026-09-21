"""Fábrica do modelo Gemini usado pelos agentes.

Os modelos `flash` do Google AI Studio devolvem 503 ("high demand") de vez em
quando. Sem tratamento, um 503 vira erro 500 na API e derruba o fluxo do
morador no meio de uma reserva. O `HttpRetryOptions` abaixo faz o cliente do
`google-genai` repetir a chamada com backoff antes de desistir.
"""

from __future__ import annotations

from google.adk.models.google_llm import Gemini
from google.genai import types

from .. import configuracao  # carrega o .env antes de qualquer cliente ser criado

RETENTATIVAS = types.HttpRetryOptions(
    attempts=6,
    initial_delay=1.0,
    max_delay=30.0,
    exp_base=2.0,
    jitter=0.5,
    http_status_codes=[408, 429, 500, 502, 503, 504],
)


def modelo_gemini(nome: str) -> Gemini:
    return Gemini(model=nome, retry_options=RETENTATIVAS)
