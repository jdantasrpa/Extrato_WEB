# INSERIR EM: automação_email_antigo/checkpoint_importacao.py
#
# Checkpoint de idempotência das etapas diárias.
#
# O e-mail do Banco Arbi chega em janela variável (≈10h–15h), então a
# automação é executada várias vezes ao dia. Este módulo registra a data
# da última execução concluída com sucesso por etapa, permitindo
# interromper as execuções seguintes do mesmo dia — evitando reimportar e
# gerar descasamento de informação para o time de conciliação, e evitando
# republicar os mesmos arquivos no chat do Teams.
#
# Sem dependência de py_rpautom: usado tanto pelo orquestrador
# (main_diario) quanto pelo importador (importar_extrato_web).

# --- stdlib ---
import json
import logging
from datetime import date
from pathlib import Path
from typing import Optional

# --------------------------------------------------------------------------- #
# Constantes
# --------------------------------------------------------------------------- #
PASTA_RAIZ = Path(r'C:\RPA\automação_email_antigo\extratos')
CAMINHO_CHECKPOINT = PASTA_RAIZ / 'estado_importacao.json'
CHAVE_ULTIMA_IMPORTACAO = 'ultima_data_importada'
CHAVE_ULTIMO_ENVIO_TEAMS = 'ultima_data_enviada_teams'

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# I/O — leitura e escrita do checkpoint
# --------------------------------------------------------------------------- #
def ler_estado(caminho: Path = CAMINHO_CHECKPOINT) -> dict:
    """Lê o checkpoint completo (todas as etapas registradas).

    Args:
        caminho: Caminho do arquivo JSON de checkpoint.

    Returns:
        Dicionário etapa -> data ISO; vazio se não houver checkpoint
        legível.
    """
    if not caminho.is_file():
        return {}
    try:
        dados = json.loads(caminho.read_text(encoding='utf-8'))
        return dados if isinstance(dados, dict) else {}
    except (ValueError, OSError) as exc:
        logger.warning('Checkpoint ilegível (%s); será ignorado.', exc)
        return {}


def ler_data_etapa(
    chave: str,
    caminho: Path = CAMINHO_CHECKPOINT,
) -> Optional[date]:
    """Lê a data da última execução concluída de uma etapa.

    Args:
        chave: Chave da etapa no checkpoint.
        caminho: Caminho do arquivo JSON de checkpoint.

    Returns:
        Data registrada, ou None se ausente ou inválida.
    """
    valor = ler_estado(caminho).get(chave)
    if not valor:
        return None
    try:
        return date.fromisoformat(valor)
    except (TypeError, ValueError):
        logger.warning('Data inválida no checkpoint para "%s".', chave)
        return None


def registrar_data_etapa(
    chave: str,
    data_referencia: date,
    caminho: Path = CAMINHO_CHECKPOINT,
) -> None:
    """Registra a data de conclusão de uma etapa, preservando as demais.

    Args:
        chave: Chave da etapa no checkpoint.
        data_referencia: Data cuja etapa foi concluída.
        caminho: Caminho do arquivo JSON de checkpoint.
    """
    caminho.parent.mkdir(parents=True, exist_ok=True)
    estado = {**ler_estado(caminho), chave: data_referencia.isoformat()}
    caminho.write_text(
        json.dumps(estado, ensure_ascii=False), encoding='utf-8'
    )
    logger.info(
        'Checkpoint atualizado: %s = %s.', chave, data_referencia.isoformat()
    )


def etapa_ja_concluida(
    chave: str,
    data_referencia: date,
    caminho: Path = CAMINHO_CHECKPOINT,
) -> bool:
    """Indica se a etapa já foi concluída na data de referência.

    Args:
        chave: Chave da etapa no checkpoint.
        data_referencia: Data a verificar.
        caminho: Caminho do arquivo JSON de checkpoint.

    Returns:
        True se a última data registrada para a etapa é a informada.
    """
    return ler_data_etapa(chave, caminho) == data_referencia


def ler_ultima_importacao(
    caminho: Path = CAMINHO_CHECKPOINT,
) -> Optional[date]:
    """Lê a data da última importação concluída com sucesso.

    Args:
        caminho: Caminho do arquivo JSON de checkpoint.

    Returns:
        Data da última importação, ou None se não houver checkpoint válido.
    """
    return ler_data_etapa(CHAVE_ULTIMA_IMPORTACAO, caminho)


def registrar_importacao_concluida(
    data_referencia: date,
    caminho: Path = CAMINHO_CHECKPOINT,
) -> None:
    """Registra no checkpoint a data da importação concluída com sucesso.

    Args:
        data_referencia: Data cuja importação foi concluída.
        caminho: Caminho do arquivo JSON de checkpoint.
    """
    registrar_data_etapa(CHAVE_ULTIMA_IMPORTACAO, data_referencia, caminho)


def registrar_envio_teams_concluido(
    data_referencia: date,
    caminho: Path = CAMINHO_CHECKPOINT,
) -> None:
    """Registra a data da publicação dos arquivos no chat do Teams.

    Args:
        data_referencia: Data cujos arquivos já foram publicados.
        caminho: Caminho do arquivo JSON de checkpoint.
    """
    registrar_data_etapa(CHAVE_ULTIMO_ENVIO_TEAMS, data_referencia, caminho)


def envio_teams_ja_concluido(
    data_referencia: date,
    caminho: Path = CAMINHO_CHECKPOINT,
) -> bool:
    """Indica se os arquivos da data já foram publicados no Teams.

    Args:
        data_referencia: Data a verificar.
        caminho: Caminho do arquivo JSON de checkpoint.

    Returns:
        True se já houve publicação bem-sucedida para a data.

    Example:
        >>> from datetime import date
        >>> envio_teams_ja_concluido(date(2026, 8, 5), Path('inexistente'))
        False
    """
    return etapa_ja_concluida(
        CHAVE_ULTIMO_ENVIO_TEAMS, data_referencia, caminho
    )


# --------------------------------------------------------------------------- #
# Decisão de fluxo
# --------------------------------------------------------------------------- #
def importacao_ja_concluida(
    data_referencia: date,
    caminho: Path = CAMINHO_CHECKPOINT,
) -> bool:
    """Indica se a importação da data de referência já foi concluída.

    Args:
        data_referencia: Data a verificar.
        caminho: Caminho do arquivo JSON de checkpoint.

    Returns:
        True se a última importação registrada é a da data de referência.

    Example:
        >>> from datetime import date
        >>> importacao_ja_concluida(date(2026, 8, 5), Path('inexistente'))
        False
    """
    return ler_ultima_importacao(caminho) == data_referencia
