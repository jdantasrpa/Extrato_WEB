# INSERIR EM: automação_email_antigo/tests/test_extrair_extratos.py
# DEPENDÊNCIA: pip install pytest pandas

# --- stdlib ---
from datetime import date

# --- terceiros ---
import pandas as pd
import pytest

# --- locais ---
import extrair_extratos as ee
import sugestao_convenios as sc

DATA_REF = date(2026, 8, 5)


# --------------------------------------------------------------------------- #
# remover_linhas_da_data (pura) — idempotência do consolidado
# --------------------------------------------------------------------------- #
def test_remover_linhas_da_data_descarta_apenas_a_data():
    df = pd.DataFrame(
        {
            'EMPRESA': ['VEM', 'VEM', 'VEM'],
            'DATA_EXTRACAO': ['04/08/2026', '05/08/2026', '05/08/2026'],
        }
    )
    resultado = ee.remover_linhas_da_data(df, '05/08/2026')

    assert list(resultado['DATA_EXTRACAO']) == ['04/08/2026']


def test_remover_linhas_da_data_nao_muta_original():
    df = pd.DataFrame({'DATA_EXTRACAO': ['05/08/2026']})
    ee.remover_linhas_da_data(df, '05/08/2026')

    assert len(df) == 1  # original intacto


def test_remover_linhas_da_data_sem_coluna_retorna_igual():
    df = pd.DataFrame({'VALOR': [1, 2]})
    resultado = ee.remover_linhas_da_data(df, '05/08/2026')

    assert len(resultado) == 2


# --------------------------------------------------------------------------- #
# classificar_convenio (pura) — match imune a zero à esquerda
# --------------------------------------------------------------------------- #
def test_classificar_convenio_casa_documento_sem_zero_a_esquerda():
    """O extrato traz '4312369000190'; o catálogo, '04312369000190'."""
    mapa = {'04312369000190': 'Gov. Amazonas'}

    assert (
        ee.classificar_convenio('4312369000190', 'TED RECEB', mapa)
        == 'Gov. Amazonas'
    )


def test_classificar_convenio_casa_documento_com_mascara():
    mapa = {'04312369000190': 'Gov. Amazonas'}

    assert (
        ee.classificar_convenio('04.312.369/0001-90', 'TED RECEB', mapa)
        == 'Gov. Amazonas'
    )


def test_classificar_convenio_cpf_continua_pessoa_fisica():
    assert (
        ee.classificar_convenio('12345678901', 'TED RECEB', {})
        == 'Pessoa Física'
    )


def test_classificar_convenio_desconhecido_fica_pendente():
    assert (
        ee.classificar_convenio('99999999000199', 'TED RECEB', {})
        == ' Validar com a Conciliação'
    )


# --------------------------------------------------------------------------- #
# reclassificar_pendencias_consolidado (I/O) — aproveita catálogo novo
# --------------------------------------------------------------------------- #
def test_reclassificar_pendencias_atualiza_apenas_pendentes(tmp_path):
    caminho = tmp_path / 'consolidado.xlsx'
    pd.DataFrame(
        {
            'CGC_CPF_CTP': ['4312369000190', '5943030000155'],
            'HISTORICO_DESCRICAO': ['TED RECEB', 'TED RECEB'],
            'Convênios': [' Validar com a Conciliação', 'Pref. Boa Vista'],
        }
    ).to_excel(caminho, index=False)

    atualizadas = ee.reclassificar_pendencias_consolidado(
        caminho, {'04312369000190': 'Gov. Amazonas'}
    )

    assert atualizadas == 1
    df = pd.read_excel(caminho, dtype=str)
    assert list(df['Convênios']) == ['Gov. Amazonas', 'Pref. Boa Vista']


def test_reclassificar_pendencias_arquivo_bloqueado_nao_quebra(
    tmp_path, monkeypatch
):
    """Consolidado aberto no Excel não pode derrubar a execução."""
    caminho = tmp_path / 'consolidado.xlsx'
    pd.DataFrame(
        {
            'CGC_CPF_CTP': ['4312369000190'],
            'HISTORICO_DESCRICAO': ['TED RECEB'],
            'Convênios': [' Validar com a Conciliação'],
        }
    ).to_excel(caminho, index=False)

    def _falhar(*_args, **_kwargs):
        raise PermissionError('arquivo em uso')

    monkeypatch.setattr(pd.DataFrame, 'to_excel', _falhar)

    assert (
        ee.reclassificar_pendencias_consolidado(
            caminho, {'04312369000190': 'Gov. Amazonas'}
        )
        == 0
    )


def test_catalogar_reclassifica_mesmo_sem_sugestao_nova(tmp_path, monkeypatch):
    """Catálogo corrigido sem sugestão nova ainda precisa limpar pendências."""
    monkeypatch.setattr(ee, 'PASTA_RAIZ', str(tmp_path))
    monkeypatch.setattr(ee, 'CONTAS', {'1': 'VEM BENEFICIOS'})
    monkeypatch.setattr(
        ee, 'carregar_convenios', lambda: {'04312369000190': 'Gov. Amazonas'}
    )
    monkeypatch.setattr(
        ee,
        'executar_catalogacao',
        lambda caminhos, simular=False, consultar_externo=True: (
            sc.ResultadoCatalogacao((), (), (), ())
        ),
    )
    caminho = tmp_path / 'VEM BENEFICIOS' / 'consolidado_VEM BENEFICIOS.xlsx'
    caminho.parent.mkdir(parents=True)
    pd.DataFrame(
        {
            'CGC_CPF_CTP': ['4312369000190'],
            'HISTORICO_DESCRICAO': ['TED RECEB'],
            'Convênios': [' Validar com a Conciliação'],
        }
    ).to_excel(caminho, index=False)

    assert ee.catalogar_convenios_pendentes() == 1


def test_reclassificar_pendencias_sem_catalogo_novo_nao_altera(tmp_path):
    caminho = tmp_path / 'consolidado.xlsx'
    pd.DataFrame(
        {
            'CGC_CPF_CTP': ['99999999000199'],
            'HISTORICO_DESCRICAO': ['TED RECEB'],
            'Convênios': [' Validar com a Conciliação'],
        }
    ).to_excel(caminho, index=False)

    assert ee.reclassificar_pendencias_consolidado(caminho, {}) == 0


# --------------------------------------------------------------------------- #
# normalizar_datas_movimentacao (pura) — data real, não texto ambíguo
# --------------------------------------------------------------------------- #
def test_normalizar_datas_converte_formato_brasileiro():
    df = pd.DataFrame({'DATA_MOVIMENTACAO': ['21/07/2026 00:00:00']})
    resultado = ee.normalizar_datas_movimentacao(df)

    assert resultado['DATA_MOVIMENTACAO'].iloc[0] == pd.Timestamp(2026, 7, 21)


def test_normalizar_datas_dia_ambiguo_nao_vira_mes():
    """'10/08/2026' é 10 de agosto — nunca 8 de outubro."""
    df = pd.DataFrame({'DATA_MOVIMENTACAO': ['10/08/2026 00:00:00']})
    resultado = ee.normalizar_datas_movimentacao(df)

    assert resultado['DATA_MOVIMENTACAO'].iloc[0].month == 8
    assert resultado['DATA_MOVIMENTACAO'].iloc[0].day == 10


def test_normalizar_datas_mantem_iso_existente():
    df = pd.DataFrame({'DATA_MOVIMENTACAO': ['2026-04-16 00:00:00']})
    resultado = ee.normalizar_datas_movimentacao(df)

    assert resultado['DATA_MOVIMENTACAO'].iloc[0] == pd.Timestamp(2026, 4, 16)


def test_normalizar_datas_coluna_mista():
    df = pd.DataFrame(
        {
            'DATA_MOVIMENTACAO': [
                '2026-04-16 00:00:00',
                '21/07/2026 00:00:00',
                '10/08/2026',
                pd.Timestamp(2026, 5, 3),
            ]
        }
    )
    resultado = ee.normalizar_datas_movimentacao(df)

    assert list(resultado['DATA_MOVIMENTACAO']) == [
        pd.Timestamp(2026, 4, 16),
        pd.Timestamp(2026, 7, 21),
        pd.Timestamp(2026, 8, 10),
        pd.Timestamp(2026, 5, 3),
    ]


def test_normalizar_datas_sentinela_sem_movimento_vira_vazio():
    """31/12/1899 é o marcador do banco para 'dia sem movimentação'."""
    df = pd.DataFrame(
        {'DATA_MOVIMENTACAO': ['31/12/1899 00:00:00', '10/08/2026']}
    )
    resultado = ee.normalizar_datas_movimentacao(df)

    assert pd.isna(resultado['DATA_MOVIMENTACAO'].iloc[0])
    assert resultado['DATA_MOVIMENTACAO'].iloc[1] == pd.Timestamp(2026, 8, 10)


def test_normalizar_datas_valor_invalido_vira_nat():
    df = pd.DataFrame({'DATA_MOVIMENTACAO': ['sem data', None]})
    resultado = ee.normalizar_datas_movimentacao(df)

    assert resultado['DATA_MOVIMENTACAO'].isna().all()


def test_normalizar_datas_nao_muta_original():
    df = pd.DataFrame({'DATA_MOVIMENTACAO': ['21/07/2026 00:00:00']})
    ee.normalizar_datas_movimentacao(df)

    assert df['DATA_MOVIMENTACAO'].iloc[0] == '21/07/2026 00:00:00'


def test_normalizar_datas_sem_coluna_retorna_igual():
    df = pd.DataFrame({'VALOR': [1, 2]})
    resultado = ee.normalizar_datas_movimentacao(df)

    assert list(resultado['VALOR']) == [1, 2]


# --------------------------------------------------------------------------- #
# consolidado_precisa_normalizacao (pura) — gate da migração idempotente
# --------------------------------------------------------------------------- #
def test_precisa_normalizacao_true_com_texto():
    df = pd.DataFrame({'DATA_MOVIMENTACAO': ['21/07/2026 00:00:00']})

    assert ee.consolidado_precisa_normalizacao(df) is True


def test_precisa_normalizacao_false_com_datetime():
    df = pd.DataFrame({'DATA_MOVIMENTACAO': pd.to_datetime(['2026-07-21'])})

    assert ee.consolidado_precisa_normalizacao(df) is False


def test_precisa_normalizacao_false_sem_coluna():
    df = pd.DataFrame({'VALOR': [1]})

    assert ee.consolidado_precisa_normalizacao(df) is False


# --------------------------------------------------------------------------- #
# migrar_datas_consolidados (I/O) — corrige o histórico já gravado
# --------------------------------------------------------------------------- #
def test_migrar_datas_reescreve_consolidado_com_texto(tmp_path, monkeypatch):
    monkeypatch.setattr(ee, 'PASTA_RAIZ', str(tmp_path))
    caminho = tmp_path / 'VEM BENEFICIOS' / 'consolidado_VEM BENEFICIOS.xlsx'
    caminho.parent.mkdir(parents=True)
    pd.DataFrame(
        {
            'DATA_MOVIMENTACAO': [
                '2026-04-16 00:00:00',
                '10/08/2026 00:00:00',
            ],
            'VALOR': [10.5, 20.25],
        }
    ).to_excel(caminho, index=False)

    normalizados = ee.migrar_datas_consolidados()

    assert 'VEM BENEFICIOS' in normalizados
    df = pd.read_excel(caminho)
    assert list(df['DATA_MOVIMENTACAO']) == [
        pd.Timestamp(2026, 4, 16),
        pd.Timestamp(2026, 8, 10),
    ]
    assert list(df['VALOR']) == [10.5, 20.25]


def test_migrar_datas_idempotente(tmp_path, monkeypatch):
    monkeypatch.setattr(ee, 'PASTA_RAIZ', str(tmp_path))
    caminho = tmp_path / 'EI CARD' / 'consolidado_EI CARD.xlsx'
    caminho.parent.mkdir(parents=True)
    pd.DataFrame({'DATA_MOVIMENTACAO': ['10/08/2026 00:00:00']}).to_excel(
        caminho, index=False
    )

    ee.migrar_datas_consolidados()

    # Segunda passada não tem o que corrigir.
    assert ee.migrar_datas_consolidados() == []


# --------------------------------------------------------------------------- #
# extrato_diario_existe / datas_alvo_para_captura (I/O leve)
# --------------------------------------------------------------------------- #
def _criar_csv_diario(raiz, data_alvo, sufixo='VEM BENEFICIOS.csv'):
    pasta_dia = raiz / data_alvo.strftime('%Y-%m-%d')
    pasta_dia.mkdir(parents=True, exist_ok=True)
    nome = f"{data_alvo.strftime('%d-%m-%Y')}_{sufixo}"
    (pasta_dia / nome).write_text('x', encoding='utf-8')


def test_extrato_diario_existe_true(tmp_path, monkeypatch):
    monkeypatch.setattr(ee, 'PASTA_RAIZ', str(tmp_path))
    _criar_csv_diario(tmp_path, DATA_REF)
    assert ee.extrato_diario_existe(DATA_REF) is True


def test_extrato_diario_existe_false_sem_pasta(tmp_path, monkeypatch):
    monkeypatch.setattr(ee, 'PASTA_RAIZ', str(tmp_path))
    assert ee.extrato_diario_existe(DATA_REF) is False


def test_extrato_diario_existe_false_outro_originador(tmp_path, monkeypatch):
    monkeypatch.setattr(ee, 'PASTA_RAIZ', str(tmp_path))
    _criar_csv_diario(tmp_path, DATA_REF, sufixo='ALVO CARD.csv')
    assert ee.extrato_diario_existe(DATA_REF) is False


def test_datas_alvo_so_hoje_quando_ontem_capturado(tmp_path, monkeypatch):
    monkeypatch.setattr(ee, 'PASTA_RAIZ', str(tmp_path))
    ontem = DATA_REF.fromordinal(DATA_REF.toordinal() - 1)
    _criar_csv_diario(tmp_path, ontem)

    assert ee.datas_alvo_para_captura(DATA_REF) == [DATA_REF]


def test_datas_alvo_inclui_ontem_quando_ausente(tmp_path, monkeypatch):
    monkeypatch.setattr(ee, 'PASTA_RAIZ', str(tmp_path))
    ontem = DATA_REF.fromordinal(DATA_REF.toordinal() - 1)

    # Ontem antes de hoje (ordem cronológica).
    assert ee.datas_alvo_para_captura(DATA_REF) == [ontem, DATA_REF]
