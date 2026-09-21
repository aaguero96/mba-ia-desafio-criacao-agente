"""Leitura dos arquivos estáticos de `dados/`.

Apartamentos, áreas e regulamento não mudam durante a execução, então são lidos
uma vez e mantidos em memória. Reservas e visitantes NÃO são lidos daqui em
tempo de execução: eles vivem no banco (ver `app/armazenamento.py`), porque são
gravados pelo assistente.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache

from . import configuracao


@dataclass(frozen=True)
class Area:
    id: str
    nome: str
    taxa: float

    @property
    def gera_cobranca(self) -> bool:
        """Regra de negócio 2: taxa maior que zero gera cobrança."""
        return self.taxa > 0


@lru_cache(maxsize=1)
def apartamentos() -> dict[str, str]:
    """Mapa `numero -> morador`."""
    bruto = json.loads(configuracao.ARQUIVO_APARTAMENTOS.read_text(encoding="utf-8"))
    return {item["numero"]: item["morador"] for item in bruto}


def apartamento_existe(numero: str) -> bool:
    return numero in apartamentos()


@lru_cache(maxsize=1)
def areas() -> dict[str, Area]:
    """Mapa `id da área -> Area`."""
    bruto = json.loads(configuracao.ARQUIVO_AREAS.read_text(encoding="utf-8"))
    return {item["id"]: Area(id=item["id"], nome=item["nome"], taxa=float(item["taxa"])) for item in bruto}


def reservas_iniciais() -> list[dict]:
    return json.loads(configuracao.ARQUIVO_RESERVAS.read_text(encoding="utf-8"))


def visitantes_iniciais() -> list[dict]:
    return json.loads(configuracao.ARQUIVO_VISITANTES.read_text(encoding="utf-8"))


def _sem_acento(texto: str) -> str:
    normalizado = unicodedata.normalize("NFKD", texto)
    return "".join(c for c in normalizado if not unicodedata.combining(c)).lower()


def resolver_area(termo: str) -> Area | None:
    """Converte o que o morador (ou o modelo) escreveu no id canônico da área.

    Aceita o id (`salao-de-festas`), o nome (`Salão de festas`) ou variações sem
    acento. Devolve `None` quando não reconhece — a tool então recusa em vez de
    inventar uma área.
    """
    alvo = _sem_acento(termo).strip()
    if not alvo:
        return None
    alvo_compacto = re.sub(r"[^a-z0-9]", "", alvo)
    for area in areas().values():
        candidatos = {
            _sem_acento(area.id),
            _sem_acento(area.nome),
            re.sub(r"[^a-z0-9]", "", _sem_acento(area.id)),
            re.sub(r"[^a-z0-9]", "", _sem_acento(area.nome)),
        }
        if alvo in candidatos or alvo_compacto in candidatos:
            return area
    # Busca parcial: "salão" -> "salao-de-festas", "quadra" -> "quadra".
    for area in areas().values():
        nome = _sem_acento(area.nome)
        if alvo and (alvo in nome or nome.startswith(alvo)):
            return area
    return None


DATA_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def data_valida(data: str) -> bool:
    if not DATA_ISO.match(data or ""):
        return False
    from datetime import date

    try:
        date.fromisoformat(data)
    except ValueError:
        return False
    return True


# --------------------------------------------------------------------------
# Regulamento: indexado por capítulo para que o assistente consulte um capítulo
# por vez em vez de carregar o documento inteiro (Garantia 4).
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Capitulo:
    titulo: str
    texto: str


@lru_cache(maxsize=1)
def capitulos_regulamento() -> list[Capitulo]:
    texto = configuracao.ARQUIVO_REGULAMENTO.read_text(encoding="utf-8")
    partes = re.split(r"^## ", texto, flags=re.MULTILINE)[1:]
    capitulos: list[Capitulo] = []
    for parte in partes:
        linhas = parte.splitlines()
        titulo = linhas[0].strip()
        capitulos.append(Capitulo(titulo=titulo, texto=("## " + parte).strip()))
    return capitulos


def indice_regulamento() -> list[str]:
    """Só os títulos dos capítulos — nenhum conteúdo."""
    return [c.titulo for c in capitulos_regulamento()]


def buscar_capitulo(termo: str) -> Capitulo | None:
    """Localiza um capítulo pelo título, pelo número romano ou por palavra-chave."""
    alvo = _sem_acento(termo).strip()
    if not alvo:
        return None
    for capitulo in capitulos_regulamento():
        if _sem_acento(capitulo.titulo) == alvo:
            return capitulo
    for capitulo in capitulos_regulamento():
        if alvo in _sem_acento(capitulo.titulo):
            return capitulo
    # Última tentativa: alguma palavra do pedido aparece no título do capítulo.
    palavras = [p for p in re.split(r"[^a-z0-9]+", alvo) if len(p) > 3]
    melhor: tuple[int, Capitulo] | None = None
    for capitulo in capitulos_regulamento():
        titulo = _sem_acento(capitulo.titulo)
        pontos = sum(1 for p in palavras if p in titulo)
        if pontos and (melhor is None or pontos > melhor[0]):
            melhor = (pontos, capitulo)
    return melhor[1] if melhor else None
