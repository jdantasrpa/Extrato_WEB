# INSERIR EM: automação_email_antigo/tests/test_consulta_cnpj.py
# DEPENDÊNCIA: pip install pytest pandas requests

# --- terceiros ---
import pytest

# --- locais ---
import sugestao_convenios as sc

CATALOGO_EXEMPLO = (
    ('04312369000190', 'Gov. Amazonas'),
    ('07782840000100', 'Pref. Morada Nova'),
    ('76175884000187', 'Pref. Ponta Grossa'),
)


@pytest.fixture(autouse=True)
def isolar_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(sc, 'CAMINHO_CACHE_CNPJ', tmp_path / 'cache.json')


def _catalogo():
    return sc.montar_catalogo(CATALOGO_EXEMPLO)


def _dados(**kwargs):
    padrao = {
        'documento': '11415567000145',
        'razao_social': 'FUNDO MUNICIPAL DE SAUDE DE MORADA NOVA',
        'municipio': 'MORADA NOVA',
        'uf': 'CE',
        'natureza_juridica': 'Fundo Público da Administração Direta Municipal',
    }
    return sc.DadosCnpj(**{**padrao, **kwargs})


# --------------------------------------------------------------------------- #
# sugerir_por_dados_externos (pura)
# --------------------------------------------------------------------------- #
def test_orgao_municipal_casa_com_prefeitura_do_municipio():
    pendencia = sc.Pendencia('11415567000145', 'CE 230870 FMS CUSTEIO SUS', 5)

    sugestao = sc.sugerir_por_dados_externos(pendencia, _dados(), _catalogo())

    assert sugestao.convenio == 'Pref. Morada Nova'
    assert sugestao.origem is sc.OrigemSugestao.CONSULTA_EXTERNA
    assert sugestao.confianca >= sc.CONFIANCA_MINIMA_AUTOMATICA
    assert 'MORADA NOVA' in sugestao.detalhe


def test_orgao_estadual_casa_com_governo_do_estado():
    pendencia = sc.Pendencia('04312419000130', 'SEDUC', 2)
    dados = _dados(
        razao_social='SECRETARIA DE ESTADO DA EDUCACAO',
        municipio='MANAUS',
        uf='AM',
        natureza_juridica='Órgão Público do Poder Executivo Estadual',
    )

    sugestao = sc.sugerir_por_dados_externos(pendencia, dados, _catalogo())

    assert sugestao.convenio == 'Gov. Amazonas'
    assert sugestao.confianca >= sc.CONFIANCA_MINIMA_AUTOMATICA


def test_empresa_privada_nao_vira_prefeitura_da_sede():
    """Risco central: FIDC sediado em município conveniado não é a prefeitura."""
    pendencia = sc.Pendencia('64509633000102', 'AMETISTA CONSIGNADO III', 4)
    dados = _dados(
        razao_social='AMETISTA CONSIGNADO III FUNDO DE INVESTIMENTO',
        municipio='MORADA NOVA',
        uf='CE',
        natureza_juridica='Fundo de Investimento',
    )

    sugestao = sc.sugerir_por_dados_externos(pendencia, dados, _catalogo())

    assert sugestao is None or (
        sugestao.confianca < sc.CONFIANCA_MINIMA_AUTOMATICA
    )


def test_orgao_estadual_de_uf_sem_convenio_nao_sugere():
    pendencia = sc.Pendencia('11405835000148', 'RJ 330600 FMS', 2)
    dados = _dados(
        razao_social='FUNDO MUNICIPAL DE SAUDE DE PARATY',
        municipio='PARATY',
        uf='RJ',
        natureza_juridica='Fundo Público da Administração Direta Municipal',
    )

    assert sc.sugerir_por_dados_externos(pendencia, dados, _catalogo()) is None


def test_ente_publico_sem_convenio_nao_cai_no_fuzzy():
    """Sabemos quem é e sabemos que não há convênio: não chutar por texto."""
    pendencia = sc.Pendencia('29138377000193', 'FMS SUS REC PROPR MUNICIP', 1)
    dados = _dados(
        razao_social='MUNICIPIO DE TRES RIOS',
        municipio='TRES RIOS',
        uf='RJ',
        natureza_juridica='Município',
    )

    assert sc.sugerir_por_dados_externos(pendencia, dados, _catalogo()) is None


def test_sem_dados_externos_retorna_none():
    pendencia = sc.Pendencia('11415567000145', 'X', 1)

    assert sc.sugerir_por_dados_externos(pendencia, None, _catalogo()) is None


# --------------------------------------------------------------------------- #
# enriquecer_sem_pista — resiliência é requisito, não detalhe
# --------------------------------------------------------------------------- #
def test_enriquecer_usa_consultor_injetado():
    pendencias = (sc.Pendencia('11415567000145', 'FMS CUSTEIO SUS', 5),)

    sugestoes = sc.enriquecer_sem_pista(
        pendencias, _catalogo(), consultor=lambda _doc: _dados()
    )

    assert sugestoes[0].convenio == 'Pref. Morada Nova'


def test_enriquecer_segue_quando_api_falha():
    """API fora do ar não pode impedir o restante do processo."""
    pendencias = (
        sc.Pendencia('11415567000145', 'FMS CUSTEIO SUS', 5),
        sc.Pendencia('11405835000148', 'OUTRO', 1),
    )

    def _consultor_instavel(documento):
        if documento == '11415567000145':
            raise OSError('conexão recusada')
        return _dados(documento=documento)

    sugestoes = sc.enriquecer_sem_pista(
        pendencias, _catalogo(), consultor=_consultor_instavel
    )

    assert len(sugestoes) == 1  # o segundo foi processado normalmente


def test_enriquecer_sem_consultor_disponivel_retorna_vazio():
    pendencias = (sc.Pendencia('11415567000145', 'FMS', 1),)

    sugestoes = sc.enriquecer_sem_pista(
        pendencias, _catalogo(), consultor=lambda _doc: None
    )

    assert sugestoes == ()


# --------------------------------------------------------------------------- #
# consultar_documento — cache e tolerância a falha (sem rede real)
# --------------------------------------------------------------------------- #
def test_consultar_documento_usa_cache(monkeypatch):
    chamadas = []

    def _fake(documento, provedor):
        chamadas.append(documento)
        return {
            'razao_social': 'FUNDO MUNICIPAL DE SAUDE DE MORADA NOVA',
            'municipio': 'MORADA NOVA',
            'uf': 'CE',
            'natureza_juridica': 'Fundo Público da Administração Direta Municipal',
        }

    monkeypatch.setattr(sc, '_baixar_dados_cnpj', _fake)

    primeiro = sc.consultar_documento('11415567000145')
    segundo = sc.consultar_documento('11415567000145')

    assert primeiro == segundo
    assert len(chamadas) == 1  # segunda veio do cache


def test_consultar_documento_todos_provedores_fora(monkeypatch):
    def _fake(documento, provedor):
        raise OSError('sem rede')

    monkeypatch.setattr(sc, '_baixar_dados_cnpj', _fake)

    assert sc.consultar_documento('11415567000145') is None
