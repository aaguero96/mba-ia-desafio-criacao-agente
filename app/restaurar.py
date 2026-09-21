"""Comando de restauração dos dados iniciais (`aurora-restaurar`).

Volta reservas e visitantes exatamente ao estado dos arquivos de `dados/` e
apaga também as sessões: um ambiente restaurado começa sem conversa nenhuma,
sem confirmação pendente herdada e sem evento antigo. Os arquivos de `dados/`
não são tocados.
"""

from __future__ import annotations

from . import armazenamento, configuracao


def main() -> None:
    configuracao.garantir_diretorio_estado()
    totais = armazenamento.restaurar_dados_iniciais()

    if configuracao.BANCO_SESSOES.exists():
        configuracao.BANCO_SESSOES.unlink()
    for sufixo in ("-wal", "-shm"):
        extra = configuracao.BANCO_SESSOES.with_name(configuracao.BANCO_SESSOES.name + sufixo)
        if extra.exists():
            extra.unlink()

    print(
        f"Dados restaurados em {configuracao.DIRETORIO_ESTADO}: "
        f"{totais['reservas']} reservas e {totais['visitantes']} visitantes. "
        "Sessões apagadas."
    )


if __name__ == "__main__":  # pragma: no cover
    main()
