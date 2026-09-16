# INSERIR EM: automação_email_antigo/tests/test_sugestao_convenios.py
# DEPENDÊNCIA: pip install pytest pandas openpyxl

# --- terceiros ---
import pandas as pd
import pytest

# --- locais ---
import sugestao_convenios as sc


@pytest.fixture(autouse=True)
def isolar_escrita_de_producao(tmp_path, monkeypatch):
    """Impede que qualquer teste escreva backup/auditoria no diretório real."""
    monkeypatch.setattr(sc, 'PASTA_BACKUP', tmp_path / 'backup')
    monkeypatch.setattr(sc, 'CAMINHO_AUDITORIA', tmp_path / 'auditoria.csv')


# Raiz 04312369 -> Gov. Amazonas (matriz já catalogada).
CATALOGO_EXEMPLO = (
    ('04312369000190', 'Gov. Amazonas'),
    ('4312369000271', 'Gov. Amazonas'),
    ('76175884000187', 'Pref. Ponta Grossa'),
    ('64509633000102', 'AMETISTA CONSIGNADO II'),
)


def _catalogo():
    return sc.montar_catalogo(CATALOGO_EXEMPLO)


# --------------------------------------------------------------------------- #
# normalizar_documento / raiz_documento (puras)
# --------------------------------------------------------------------------- #
def test_normalizar_documento_completa_zeros_a_esquerda():
    assert sc.normalizar_documento('4312369000190') == '04312369000190'


def test_normalizar_documento_remove_mascara():
    assert sc.normalizar_documento('04.312.369/0001-90') == '04312369000190'


def test_normalizar_documento_preserva_cpf():
    assert sc.normalizar_documento('12345678901') == '12345678901'


def test_raiz_documento_retorna_oito_digitos():
    assert sc.raiz_documento('04312369000190') == '04312369'


def test_raiz_documento_rejeita_raiz_zerada():
    assert sc.raiz_documento('00000000000123') == ''


def test_raiz_documento_rejeita_documento_curto():
    assert sc.raiz_documento('12345678901') == ''


# --------------------------------------------------------------------------- #
# montar_catalogo (pura)
# --------------------------------------------------------------------------- #
def test_catalogo_indexa_por_documento_normalizado():
    catalogo = _catalogo()

    assert catalogo.por_documento['04312369000190'] == 'Gov. Amazonas'
    assert catalogo.por_documento['04312369000271'] == 'Gov. Amazonas'


def test_catalogo_indexa_raiz_unanime():
    assert _catalogo().por_raiz['04312369'] == 'Gov. Amazonas'


def test_catalogo_descarta_raiz_ambigua():
    catalogo = sc.montar_catalogo(
        (
            ('04312369000190', 'Gov. Amazonas'),
            ('04312369000271', 'Pref. Manaus'),
        )
    )

    assert '04312369' not in catalogo.por_raiz


# --------------------------------------------------------------------------- #
# sugerir (pura) — coração da classificação
# --------------------------------------------------------------------------- #
def test_sugerir_por_raiz_do_cnpj():
    pendencia = sc.Pendencia('04312419000130', 'SEDUC', 5)
    catalogo = sc.montar_catalogo((('04312419000190', 'Gov. Amazonas'),))

    sugestao = sc.sugerir(pendencia, catalogo)

    assert sugestao.convenio == 'Gov. Amazonas'
    assert sugestao.origem is sc.OrigemSugestao.RAIZ_CNPJ
    assert sugestao.confianca >= sc.CONFIANCA_MINIMA_AUTOMATICA


def test_sugerir_por_semelhanca_de_nome_nao_e_automatica():
    pendencia = sc.Pendencia('99999999000199', 'MUNICIPIO DE PONTA GROSSA', 3)

    sugestao = sc.sugerir(pendencia, _catalogo())

    assert sugestao.convenio == 'Pref. Ponta Grossa'
    assert sugestao.origem is sc.OrigemSugestao.SEMELHANCA_NOME
    assert sugestao.confianca < sc.CONFIANCA_MINIMA_AUTOMATICA


def test_sugerir_ignora_documento_ja_catalogado():
    pendencia = sc.Pendencia('04312369000190', 'GOV AM', 1)

    assert sc.sugerir(pendencia, _catalogo()) is None


def test_sugerir_sem_pista_retorna_none():
    pendencia = sc.Pendencia('11222333000181', 'FUNDO REC PROPRIO', 2)

    assert sc.sugerir(pendencia, _catalogo()) is None


def test_sugerir_nao_confunde_sequencia_de_nome_diferente():
    """AMETISTA III é convênio novo — não pode virar o II."""
    pendencia = sc.Pendencia(
        '11222333000181', 'AMETISTA CONSIGNADO III FUNDO DE IN', 4
    )

    sugestao = sc.sugerir(pendencia, _catalogo())

    assert sugestao is None or sugestao.origem is (
        sc.OrigemSugestao.SEMELHANCA_NOME
    )


# --------------------------------------------------------------------------- #
# separar_por_confianca (pura) — política de auto-aplicação
# --------------------------------------------------------------------------- #
def test_separar_por_confianca():
    automatica = sc.Sugestao(
        '1', 'A', 'Gov. X', sc.OrigemSugestao.RAIZ_CNPJ, 0.95, 1
    )
    revisao = sc.Sugestao(
        '2', 'B', 'Pref. Y', sc.OrigemSugestao.SEMELHANCA_NOME, 0.8, 1
    )

    aplicaveis, pendentes = sc.separar_por_confianca((automatica, revisao))

    assert aplicaveis == (automatica,)
    assert pendentes == (revisao,)


# --------------------------------------------------------------------------- #
# consolidar_pendencias (pura)
# --------------------------------------------------------------------------- #
def test_consolidar_pendencias_agrupa_e_conta():
    linhas = (
        ('4312419000130', 'SEDUC'),
        ('04312419000130', 'SEDUC'),
        ('5943030000155', 'MUNICIPIO DE BOA VISTA'),
    )

    pendencias = sc.consolidar_pendencias(linhas)

    assert len(pendencias) == 2
    assert pendencias[0].documento == '04312419000130'
    assert pendencias[0].ocorrencias == 2


def test_consolidar_pendencias_descarta_documento_vazio():
    assert sc.consolidar_pendencias((('', 'X'), (None, 'Y'))) == ()


# --------------------------------------------------------------------------- #
# I/O — catálogo em disco
# --------------------------------------------------------------------------- #
def _criar_arquivo_catalogo(caminho):
    pd.DataFrame(
        {
            'DocumentoFederal': [doc for doc, _ in CATALOGO_EXEMPLO],
            'Convênios': [conv for _, conv in CATALOGO_EXEMPLO],
        }
    ).to_excel(caminho, sheet_name=sc.GUIA_CATALOGO, index=False)


def test_carregar_catalogo(tmp_path):
    caminho = tmp_path / "CNPJ'S.xlsx"
    _criar_arquivo_catalogo(caminho)

    catalogo = sc.carregar_catalogo(caminho)

    assert catalogo.por_documento['04312369000190'] == 'Gov. Amazonas'


def test_aplicar_no_catalogo_acrescenta_e_faz_backup(tmp_path):
    caminho = tmp_path / "CNPJ'S.xlsx"
    _criar_arquivo_catalogo(caminho)
    sugestao = sc.Sugestao(
        '04312419000130',
        'SEDUC',
        'Gov. Amazonas',
        sc.OrigemSugestao.RAIZ_CNPJ,
        0.95,
        5,
    )

    backup = sc.aplicar_no_catalogo(caminho, (sugestao,))

    assert backup.is_file()
    df = pd.read_excel(caminho, sheet_name=sc.GUIA_CATALOGO, dtype=str)
    assert '04312419000130' in set(df['DocumentoFederal'])
    assert len(df) == len(CATALOGO_EXEMPLO) + 1


def test_aplicar_no_catalogo_e_idempotente(tmp_path):
    caminho = tmp_path / "CNPJ'S.xlsx"
    _criar_arquivo_catalogo(caminho)
    sugestao = sc.Sugestao(
        '04312369000190',
        'GOV AM',
        'Gov. Amazonas',
        sc.OrigemSugestao.RAIZ_CNPJ,
        0.95,
        1,
    )

    sc.aplicar_no_catalogo(caminho, (sugestao,))

    df = pd.read_excel(caminho, sheet_name=sc.GUIA_CATALOGO, dtype=str)
    assert len(df) == len(CATALOGO_EXEMPLO)


def _criar_consolidado(caminho, linhas):
    pd.DataFrame(
        {
            'CGC_CPF_CTP': [doc for doc, _ in linhas],
            'NOME_CTP': [nome for _, nome in linhas],
            'Convênios': [sc.CONVENIO_PENDENTE] * len(linhas),
        }
    ).to_excel(caminho, index=False)


def test_executar_catalogacao_aplica_alta_confianca(tmp_path):
    catalogo = tmp_path / "CNPJ'S.xlsx"
    _criar_arquivo_catalogo(catalogo)
    consolidado = tmp_path / 'consolidado.xlsx'
    _criar_consolidado(
        consolidado,
        [
            ('4312369000352', 'SEDUC AMAZONAS'),  # mesma raiz -> automática
            ('99999999000199', 'MUNICIPIO DE PONTA GROSSA'),  # revisão
        ],
    )

    resultado = sc.executar_catalogacao(
        (consolidado,), caminho_catalogo=catalogo
    )

    assert len(resultado.aplicadas) == 1
    assert resultado.aplicadas[0].origem is sc.OrigemSugestao.RAIZ_CNPJ
    assert len(resultado.para_revisao) == 1

    df = pd.read_excel(catalogo, sheet_name=sc.GUIA_CATALOGO, dtype=str)
    assert '04312369000352' in set(df[sc.COLUNA_DOCUMENTO])
    assert (tmp_path / 'auditoria.csv').is_file()


def test_executar_catalogacao_simular_nao_grava(tmp_path):
    catalogo = tmp_path / "CNPJ'S.xlsx"
    _criar_arquivo_catalogo(catalogo)
    consolidado = tmp_path / 'consolidado.xlsx'
    _criar_consolidado(consolidado, [('4312369000352', 'SEDUC AMAZONAS')])

    resultado = sc.executar_catalogacao(
        (consolidado,), caminho_catalogo=catalogo, simular=True
    )

    assert len(resultado.aplicadas) == 1  # apenas proposta
    df = pd.read_excel(catalogo, sheet_name=sc.GUIA_CATALOGO, dtype=str)
    assert len(df) == len(CATALOGO_EXEMPLO)  # nada gravado
    assert not (tmp_path / 'auditoria.csv').exists()


def test_gravar_sugestoes_para_revisao_cria_guia(tmp_path):
    caminho = tmp_path / "CNPJ'S.xlsx"
    _criar_arquivo_catalogo(caminho)
    sugestao = sc.Sugestao(
        '99999999000199',
        'MUNICIPIO DE PONTA GROSSA',
        'Pref. Ponta Grossa',
        sc.OrigemSugestao.SEMELHANCA_NOME,
        0.8,
        3,
    )

    sc.gravar_sugestoes_para_revisao(caminho, (sugestao,))

    df = pd.read_excel(caminho, sheet_name=sc.GUIA_REVISAO, dtype=str)
    assert list(df['DocumentoFederal']) == ['99999999000199']
    assert list(df['Status']) == [sc.STATUS_PENDENTE]
    # A guia de catálogo continua intacta e o backup foi criado.
    assert len(pd.read_excel(caminho, sheet_name=sc.GUIA_CATALOGO)) == len(
        CATALOGO_EXEMPLO
    )
    assert any(sc.PASTA_BACKUP.iterdir())
