"""Armazenamento das reservas e dos visitantes em SQLite.

Duas decisões importantes moram aqui:

* **Garantia 5 (exclusividade)** — o índice `ux_reserva_area_data_ativa` é
  UNIQUE e parcial (`WHERE ativa = 1`). A exclusividade da reserva não depende
  de uma conferência feita antes do INSERT: ela é aplicada pelo SQLite no exato
  instante da gravação. Duas gravações simultâneas para a mesma área e data
  fazem uma delas levantar `IntegrityError`, que vira uma recusa normal.

* **Idempotência** — a coluna `chave_idem` é UNIQUE e recebe o
  `function_call_id` da chamada de tool. O ADK só garante execução
  *at-least-once* ao retomar uma invocação confirmada; a chave faz a segunda
  execução devolver a reserva já criada em vez de criar outra.

O código de reserva nunca é reaproveitado: `codigo` é PRIMARY KEY e o
cancelamento apenas marca `ativa = 0`, mantendo a linha (e o código) no banco.
"""

from __future__ import annotations

import asyncio
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Iterator

from . import configuracao, dados

ESQUEMA = """
CREATE TABLE IF NOT EXISTS reservas (
    codigo       TEXT PRIMARY KEY,
    apartamento  TEXT NOT NULL,
    area         TEXT NOT NULL,
    data         TEXT NOT NULL,
    ativa        INTEGER NOT NULL DEFAULT 1,
    chave_idem   TEXT UNIQUE
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_reserva_area_data_ativa
    ON reservas (area, data) WHERE ativa = 1;

CREATE INDEX IF NOT EXISTS ix_reserva_apartamento ON reservas (apartamento);

CREATE TABLE IF NOT EXISTS visitantes (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    apartamento  TEXT NOT NULL,
    nome         TEXT NOT NULL,
    data         TEXT NOT NULL,
    chave_idem   TEXT UNIQUE
);

CREATE INDEX IF NOT EXISTS ix_visitante_apartamento ON visitantes (apartamento);
"""


class ReservaIndisponivel(Exception):
    """A área já tem reserva ativa naquela data."""


@dataclass(frozen=True)
class Reserva:
    codigo: str
    apartamento: str
    area: str
    data: str

    def para_api(self) -> dict[str, str]:
        return {"codigo": self.codigo, "area": self.area, "data": self.data}


@contextmanager
def _conectar() -> Iterator[sqlite3.Connection]:
    configuracao.garantir_diretorio_estado()
    conexao = sqlite3.connect(
        configuracao.BANCO_CONDOMINIO,
        timeout=30.0,
        isolation_level=None,  # controlamos as transações na mão
    )
    conexao.row_factory = sqlite3.Row
    try:
        conexao.execute("PRAGMA journal_mode = WAL")
        conexao.execute("PRAGMA synchronous = FULL")
        conexao.execute("PRAGMA busy_timeout = 30000")
        yield conexao
    finally:
        conexao.close()


def preparar_banco() -> None:
    with _conectar() as conexao:
        conexao.executescript(ESQUEMA)


def _novo_codigo(conexao: sqlite3.Connection) -> str:
    """Código novo, em formato livre, que nunca repete outro já emitido."""
    while True:
        codigo = f"RSV-{uuid.uuid4().hex[:8].upper()}"
        existe = conexao.execute(
            "SELECT 1 FROM reservas WHERE codigo = ?", (codigo,)
        ).fetchone()
        if not existe:
            return codigo


# --------------------------------------------------------------------------
# Operações síncronas (todas rodam em thread separada, ver seção async abaixo)
# --------------------------------------------------------------------------


def _listar_reservas(apartamento: str) -> list[Reserva]:
    with _conectar() as conexao:
        linhas = conexao.execute(
            "SELECT codigo, apartamento, area, data FROM reservas "
            "WHERE apartamento = ? AND ativa = 1 ORDER BY data, area",
            (apartamento,),
        ).fetchall()
    return [Reserva(**dict(linha)) for linha in linhas]


def _data_ocupada(area: str, data: str) -> bool:
    """Só devolve se está ocupada — nunca de quem é a reserva (Garantia 2)."""
    with _conectar() as conexao:
        linha = conexao.execute(
            "SELECT 1 FROM reservas WHERE area = ? AND data = ? AND ativa = 1",
            (area, data),
        ).fetchone()
    return linha is not None


def _criar_reserva(apartamento: str, area: str, data: str, chave_idem: str) -> Reserva:
    with _conectar() as conexao:
        try:
            conexao.execute("BEGIN IMMEDIATE")
            ja_feita = conexao.execute(
                "SELECT codigo, apartamento, area, data FROM reservas "
                "WHERE chave_idem = ? AND ativa = 1",
                (chave_idem,),
            ).fetchone()
            if ja_feita:
                conexao.execute("COMMIT")
                return Reserva(**dict(ja_feita))

            codigo = _novo_codigo(conexao)
            conexao.execute(
                "INSERT INTO reservas (codigo, apartamento, area, data, ativa, chave_idem) "
                "VALUES (?, ?, ?, ?, 1, ?)",
                (codigo, apartamento, area, data, chave_idem),
            )
            conexao.execute("COMMIT")
        except sqlite3.IntegrityError as erro:
            conexao.execute("ROLLBACK")
            mensagem = str(erro)
            if "chave_idem" in mensagem:
                # A mesma chamada de tool foi reexecutada em paralelo: devolve a
                # reserva que a primeira execução gravou, sem criar outra.
                linha = conexao.execute(
                    "SELECT codigo, apartamento, area, data FROM reservas WHERE chave_idem = ?",
                    (chave_idem,),
                ).fetchone()
                if linha:
                    return Reserva(**dict(linha))
            if "area" in mensagem and "data" in mensagem:
                # O índice parcial UNIQUE é a única coisa que decide quem venceu
                # a disputa. Quem perdeu recebe recusa de negócio, não erro 500.
                raise ReservaIndisponivel(data) from erro
            raise
        except Exception:
            conexao.execute("ROLLBACK")
            raise
    return Reserva(codigo=codigo, apartamento=apartamento, area=area, data=data)


def _cancelar_reserva(apartamento: str, area: str, data: str) -> Reserva | None:
    """Cancela só dentro do apartamento informado — nunca de outro (Garantia 2)."""
    with _conectar() as conexao:
        conexao.execute("BEGIN IMMEDIATE")
        linha = conexao.execute(
            "SELECT codigo, apartamento, area, data FROM reservas "
            "WHERE apartamento = ? AND area = ? AND data = ? AND ativa = 1",
            (apartamento, area, data),
        ).fetchone()
        if linha is None:
            conexao.execute("COMMIT")
            return None
        conexao.execute(
            "UPDATE reservas SET ativa = 0 WHERE codigo = ?", (linha["codigo"],)
        )
        conexao.execute("COMMIT")
    return Reserva(**dict(linha))


def _listar_visitantes(apartamento: str) -> list[dict[str, str]]:
    with _conectar() as conexao:
        linhas = conexao.execute(
            "SELECT nome, data FROM visitantes WHERE apartamento = ? ORDER BY data, nome",
            (apartamento,),
        ).fetchall()
    return [{"nome": linha["nome"], "data": linha["data"]} for linha in linhas]


def _autorizar_visitante(
    apartamento: str, nome: str, data: str, chave_idem: str
) -> dict[str, str]:
    with _conectar() as conexao:
        conexao.execute("BEGIN IMMEDIATE")
        ja_feito = conexao.execute(
            "SELECT nome, data FROM visitantes WHERE chave_idem = ?", (chave_idem,)
        ).fetchone()
        if ja_feito:
            conexao.execute("COMMIT")
            return {"nome": ja_feito["nome"], "data": ja_feito["data"]}
        try:
            conexao.execute(
                "INSERT INTO visitantes (apartamento, nome, data, chave_idem) VALUES (?, ?, ?, ?)",
                (apartamento, nome, data, chave_idem),
            )
            conexao.execute("COMMIT")
        except sqlite3.IntegrityError:
            # Mesma chamada de tool reexecutada: a autorização já existe.
            conexao.execute("ROLLBACK")
    return {"nome": nome, "data": data}


def restaurar_dados_iniciais() -> dict[str, int]:
    """Volta reservas e visitantes ao estado dos arquivos de `dados/`."""
    configuracao.garantir_diretorio_estado()
    with _conectar() as conexao:
        conexao.executescript(ESQUEMA)
        conexao.execute("BEGIN IMMEDIATE")
        conexao.execute("DELETE FROM reservas")
        conexao.execute("DELETE FROM visitantes")
        conexao.execute("DELETE FROM sqlite_sequence WHERE name = 'visitantes'")
        reservas = dados.reservas_iniciais()
        conexao.executemany(
            "INSERT INTO reservas (codigo, apartamento, area, data, ativa, chave_idem) "
            "VALUES (?, ?, ?, ?, 1, NULL)",
            [(r["codigo"], r["apartamento"], r["area"], r["data"]) for r in reservas],
        )
        visitantes = dados.visitantes_iniciais()
        conexao.executemany(
            "INSERT INTO visitantes (apartamento, nome, data, chave_idem) VALUES (?, ?, ?, NULL)",
            [(v["apartamento"], v["nome"], v["data"]) for v in visitantes],
        )
        conexao.execute("COMMIT")
    return {"reservas": len(reservas), "visitantes": len(visitantes)}


# --------------------------------------------------------------------------
# Fachada assíncrona: o SQLite é síncrono, então cada operação vai para uma
# thread. Assim duas aprovações simultâneas de sessões diferentes chegam
# de fato ao mesmo tempo no banco e o índice UNIQUE decide o vencedor.
# --------------------------------------------------------------------------


async def _em_thread(funcao, *args: Any):
    return await asyncio.to_thread(funcao, *args)


async def listar_reservas(apartamento: str) -> list[Reserva]:
    return await _em_thread(_listar_reservas, apartamento)


async def data_ocupada(area: str, data: str) -> bool:
    return await _em_thread(_data_ocupada, area, data)


async def criar_reserva(apartamento: str, area: str, data: str, chave_idem: str) -> Reserva:
    return await _em_thread(_criar_reserva, apartamento, area, data, chave_idem)


async def cancelar_reserva(apartamento: str, area: str, data: str) -> Reserva | None:
    return await _em_thread(_cancelar_reserva, apartamento, area, data)


async def listar_visitantes(apartamento: str) -> list[dict[str, str]]:
    return await _em_thread(_listar_visitantes, apartamento)


async def autorizar_visitante(
    apartamento: str, nome: str, data: str, chave_idem: str
) -> dict[str, str]:
    return await _em_thread(_autorizar_visitante, apartamento, nome, data, chave_idem)
