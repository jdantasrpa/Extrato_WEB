# INSERIR EM: automação_email_antigo/tests/test_checkpoint_importacao.py
# DEPENDÊNCIA: pip install pytest

# --- stdlib ---
from datetime import date

# --- locais ---
from checkpoint_importacao import (
    envio_teams_ja_concluido,
    importacao_ja_concluida,
    ler_ultima_importacao,
    registrar_envio_teams_concluido,
    registrar_importacao_concluida,
)

DATA_REF = date(2026, 8, 5)


def test_ler_sem_arquivo_retorna_none(tmp_path):
    assert ler_ultima_importacao(tmp_path / 'inexistente.json') is None


def test_registrar_e_ler_roundtrip(tmp_path):
    caminho = tmp_path / 'estado.json'
    registrar_importacao_concluida(DATA_REF, caminho)
    assert ler_ultima_importacao(caminho) == DATA_REF


def test_ler_arquivo_corrompido_retorna_none(tmp_path):
    caminho = tmp_path / 'estado.json'
    caminho.write_text('{isto não é json', encoding='utf-8')
    assert ler_ultima_importacao(caminho) is None


def test_ja_concluida_verdadeiro_para_mesma_data(tmp_path):
    caminho = tmp_path / 'estado.json'
    registrar_importacao_concluida(DATA_REF, caminho)
    assert importacao_ja_concluida(DATA_REF, caminho) is True


def test_ja_concluida_falso_para_outra_data(tmp_path):
    caminho = tmp_path / 'estado.json'
    registrar_importacao_concluida(DATA_REF, caminho)
    assert importacao_ja_concluida(date(2026, 8, 6), caminho) is False


def test_ja_concluida_falso_sem_checkpoint(tmp_path):
    assert importacao_ja_concluida(DATA_REF, tmp_path / 'nada.json') is False


# --------------------------------------------------------------------------- #
# Etapa de publicação no Teams
# --------------------------------------------------------------------------- #
def test_envio_teams_falso_sem_checkpoint(tmp_path):
    assert envio_teams_ja_concluido(DATA_REF, tmp_path / 'nada.json') is False


def test_envio_teams_roundtrip(tmp_path):
    caminho = tmp_path / 'estado.json'
    registrar_envio_teams_concluido(DATA_REF, caminho)

    assert envio_teams_ja_concluido(DATA_REF, caminho) is True
    assert envio_teams_ja_concluido(date(2026, 8, 6), caminho) is False


def test_etapas_convivem_no_mesmo_checkpoint(tmp_path):
    caminho = tmp_path / 'estado.json'
    registrar_envio_teams_concluido(DATA_REF, caminho)
    registrar_importacao_concluida(DATA_REF, caminho)

    assert envio_teams_ja_concluido(DATA_REF, caminho) is True
    assert importacao_ja_concluida(DATA_REF, caminho) is True


def test_registrar_importacao_preserva_data_do_teams(tmp_path):
    caminho = tmp_path / 'estado.json'
    registrar_envio_teams_concluido(date(2026, 8, 6), caminho)
    registrar_importacao_concluida(DATA_REF, caminho)

    assert envio_teams_ja_concluido(date(2026, 8, 6), caminho) is True
    assert ler_ultima_importacao(caminho) == DATA_REF
