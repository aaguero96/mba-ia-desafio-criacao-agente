"""Caminhos, variáveis de ambiente e constantes do projeto."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

RAIZ = Path(__file__).resolve().parent.parent

load_dotenv(RAIZ / ".env")

# Arquivos originais do condomínio. São somente leitura: nada aqui é alterado
# em tempo de execução (o enunciado exige que `dados/` fique idêntico ao
# repositório base).
DIRETORIO_DADOS = RAIZ / "dados"
ARQUIVO_APARTAMENTOS = DIRETORIO_DADOS / "apartamentos.json"
ARQUIVO_AREAS = DIRETORIO_DADOS / "areas.json"
ARQUIVO_RESERVAS = DIRETORIO_DADOS / "reservas.json"
ARQUIVO_VISITANTES = DIRETORIO_DADOS / "visitantes.json"
ARQUIVO_REGULAMENTO = DIRETORIO_DADOS / "regulamento.md"

# Tudo que o assistente grava vive aqui, fora do Git.
DIRETORIO_ESTADO = Path(os.getenv("AURORA_DIR_ESTADO", RAIZ / "estado"))
BANCO_CONDOMINIO = DIRETORIO_ESTADO / "condominio.db"
BANCO_SESSOES = DIRETORIO_ESTADO / "sessoes.db"

NOME_APP = "aurora"
USUARIO_PADRAO = "morador"

MODELO_PRINCIPAL = os.getenv("AURORA_MODELO_PRINCIPAL", "gemini-3.5-flash-lite")
MODELO_ESPECIALISTA = os.getenv("AURORA_MODELO_ESPECIALISTA", "gemini-3.5-flash-lite")

# Chave de sessão que guarda o apartamento autenticado. É gravada uma única vez,
# na criação da sessão, e é a única fonte do apartamento para todas as tools.
CHAVE_APARTAMENTO = "apartamento"


def garantir_diretorio_estado() -> None:
    DIRETORIO_ESTADO.mkdir(parents=True, exist_ok=True)
