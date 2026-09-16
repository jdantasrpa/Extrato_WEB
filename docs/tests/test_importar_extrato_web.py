# INSERIR EM: automação_email_antigo/tests/test_importar_extrato_web.py
# DEPENDÊNCIA: pip install pytest

# --- stdlib ---
from datetime import date
from pathlib import Path

# --- terceiros ---
import pytest

# --- locais ---
import importar_extrato_web as iew
from importar_extrato_web import (
    ROTULO_EXTRATO,
    ROTULO_EXTRATO_DIA,
    ROTULO_RETORNO,
    ItemObrigatorio,
    ResultadoValidacao,
    carregar_credenciais,
    localizar_extrato_bancario,
    localizar_extrato_diario_do_dia,
    localizar_retorno_bpo,
    montar_arquivos_importacao,
    montar_itens_obrigatorios,
    parsear_data_referencia,
    validar_arquivos_obrigatorios,
)

NOME_RETORNO_VALIDO = '03082026_102900_consolidacao_arquivo_retorno.csv'
NOME_EXTRATO = 'consolidado_VEM BENEFICIOS.xlsx'
DATA_REF = date(2026, 8, 5)


def _criar_arquivo(pasta: Path, nome: str, conteudo: str = 'x') -> Path:
    caminho = pasta / nome
    caminho.write_text(conteudo, encoding='utf-8')
    return caminho


# --------------------------------------------------------------------------- #
# validar_arquivos_obrigatorios (pura)
# --------------------------------------------------------------------------- #
def test_validar_todos_presentes_fica_disponivel():
    itens = (
        ItemObrigatorio(ROTULO_EXTRATO, Path('a.xlsx')),
        ItemObrigatorio(ROTULO_RETORNO, Path('b.csv')),
    )
    resultado = validar_arquivos_obrigatorios(itens)

    assert resultado.disponivel is True
    assert len(resultado.encontrados) == 2
    assert resultado.ausentes == ()


def test_validar_um_ausente_bloqueia_e_lista():
    itens = (
        ItemObrigatorio(ROTULO_EXTRATO, Path('a.xlsx')),
        ItemObrigatorio(ROTULO_RETORNO, None),
    )
    resultado = validar_arquivos_obrigatorios(itens)

    assert resultado.disponivel is False
    rotulos_ausentes = [i.rotulo for i in resultado.ausentes]
    assert rotulos_ausentes == [ROTULO_RETORNO]


def test_validar_todos_ausentes():
    itens = (
        ItemObrigatorio(ROTULO_EXTRATO, None),
        ItemObrigatorio(ROTULO_RETORNO, None),
    )
    resultado = validar_arquivos_obrigatorios(itens)

    assert resultado.disponivel is False
    assert len(resultado.ausentes) == 2


# --------------------------------------------------------------------------- #
# montar_arquivos_importacao (pura)
# --------------------------------------------------------------------------- #
def test_montar_arquivos_importacao_extrai_caminhos():
    resultado = ResultadoValidacao(
        encontrados=(
            ItemObrigatorio(ROTULO_EXTRATO, Path('a.xlsx')),
            ItemObrigatorio(ROTULO_RETORNO, Path('b.csv')),
        ),
        ausentes=(),
    )
    arquivos = montar_arquivos_importacao(resultado)

    assert arquivos.extrato_bancario == Path('a.xlsx')
    assert arquivos.retorno_bpo == Path('b.csv')


def test_montar_arquivos_importacao_falha_se_indisponivel():
    resultado = ResultadoValidacao(
        encontrados=(ItemObrigatorio(ROTULO_EXTRATO, Path('a.xlsx')),),
        ausentes=(ItemObrigatorio(ROTULO_RETORNO, None),),
    )
    with pytest.raises(ValueError):
        montar_arquivos_importacao(resultado)


# --------------------------------------------------------------------------- #
# localizar_extrato_bancario (I/O)
# --------------------------------------------------------------------------- #
def test_localizar_extrato_encontra(tmp_path):
    esperado = _criar_arquivo(tmp_path, NOME_EXTRATO)
    assert localizar_extrato_bancario(tmp_path) == esperado


def test_localizar_extrato_nao_encontra(tmp_path):
    assert localizar_extrato_bancario(tmp_path) is None


# --------------------------------------------------------------------------- #
# localizar_retorno_bpo (I/O)
# --------------------------------------------------------------------------- #
def test_localizar_retorno_encontra_no_padrao(tmp_path):
    esperado = _criar_arquivo(tmp_path, NOME_RETORNO_VALIDO)
    assert localizar_retorno_bpo(tmp_path) == esperado


def test_localizar_retorno_ignora_fora_do_padrao(tmp_path):
    _criar_arquivo(tmp_path, 'qualquer_outro_arquivo.csv')
    _criar_arquivo(tmp_path, 'relatorio.xlsx')
    assert localizar_retorno_bpo(tmp_path) is None


def test_localizar_retorno_escolhe_mais_recente(tmp_path):
    import os
    import time

    antigo = _criar_arquivo(
        tmp_path, '01082026_090000_consolidacao_arquivo_retorno.csv'
    )
    recente = _criar_arquivo(
        tmp_path, '03082026_102900_consolidacao_arquivo_retorno.csv'
    )
    # Garante mtimes distintos e determinísticos.
    agora = time.time()
    os.utime(antigo, (agora - 100, agora - 100))
    os.utime(recente, (agora, agora))

    assert localizar_retorno_bpo(tmp_path) == recente


def test_localizar_retorno_pasta_inexistente(tmp_path):
    assert localizar_retorno_bpo(tmp_path / 'nao_existe') is None


# --------------------------------------------------------------------------- #
# carregar_credenciais (I/O)
# --------------------------------------------------------------------------- #
def test_credenciais_via_env(monkeypatch, tmp_path):
    monkeypatch.setenv('ALVO_PAINEL_USUARIO', 'Master')
    monkeypatch.setenv('ALVO_PAINEL_SENHA', 'segredo')
    cred = carregar_credenciais(tmp_path / 'inexistente.ini')

    assert cred.usuario == 'Master'
    assert cred.senha == 'segredo'


def test_credenciais_via_ini(monkeypatch, tmp_path):
    monkeypatch.delenv('ALVO_PAINEL_USUARIO', raising=False)
    monkeypatch.delenv('ALVO_PAINEL_SENHA', raising=False)
    config = tmp_path / 'config.ini'
    config.write_text(
        '[PAINEL]\nusuario = Master\nsenha = viaIni\n', encoding='utf-8'
    )
    cred = carregar_credenciais(config)

    assert cred.usuario == 'Master'
    assert cred.senha == 'viaIni'


def test_credenciais_sem_senha_falha(monkeypatch, tmp_path):
    monkeypatch.delenv('ALVO_PAINEL_USUARIO', raising=False)
    monkeypatch.delenv('ALVO_PAINEL_SENHA', raising=False)
    with pytest.raises(RuntimeError):
        carregar_credenciais(tmp_path / 'inexistente.ini')


# --------------------------------------------------------------------------- #
# localizar_extrato_diario_do_dia (I/O) — gate do e-mail Arbi do dia
# --------------------------------------------------------------------------- #
def _criar_extrato_diario(raiz: Path, data_ref: date, sufixo: str) -> Path:
    pasta_dia = raiz / data_ref.strftime('%Y-%m-%d')
    pasta_dia.mkdir(parents=True, exist_ok=True)
    nome = f"{data_ref.strftime('%d-%m-%Y')}_{sufixo}"
    return _criar_arquivo(pasta_dia, nome)


def test_extrato_diario_encontra_quando_email_chegou(tmp_path):
    esperado = _criar_extrato_diario(tmp_path, DATA_REF, 'VEM BENEFICIOS.csv')
    assert localizar_extrato_diario_do_dia(DATA_REF, tmp_path) == esperado


def test_extrato_diario_ausente_quando_pasta_do_dia_nao_existe(tmp_path):
    assert localizar_extrato_diario_do_dia(DATA_REF, tmp_path) is None


def test_extrato_diario_ignora_outra_data(tmp_path):
    _criar_extrato_diario(tmp_path, date(2026, 8, 4), 'VEM BENEFICIOS.csv')
    assert localizar_extrato_diario_do_dia(DATA_REF, tmp_path) is None


def test_extrato_diario_ignora_outro_originador(tmp_path):
    _criar_extrato_diario(tmp_path, DATA_REF, 'ALVO CARD.csv')
    assert localizar_extrato_diario_do_dia(DATA_REF, tmp_path) is None


# --------------------------------------------------------------------------- #
# montar_itens_obrigatorios (I/O) — inclui o gate do e-mail do dia
# --------------------------------------------------------------------------- #
def test_itens_obrigatorios_bloqueia_sem_email_do_dia(tmp_path):
    # Extrato acumulado e retorno BPO existem (persistem entre dias)...
    (tmp_path / 'VEM BENEFICIOS').mkdir()
    _criar_arquivo(tmp_path / 'VEM BENEFICIOS', NOME_EXTRATO)
    (tmp_path / 'BPO').mkdir()
    _criar_arquivo(tmp_path / 'BPO', NOME_RETORNO_VALIDO)
    # ...mas nenhum CSV diário do dia -> e-mail Arbi de hoje não chegou.
    itens = montar_itens_obrigatorios(
        DATA_REF,
        pasta_extrato=tmp_path / 'VEM BENEFICIOS',
        pasta_retorno=tmp_path / 'BPO',
        pasta_raiz=tmp_path,
    )
    resultado = validar_arquivos_obrigatorios(itens)

    assert resultado.disponivel is False
    assert ROTULO_EXTRATO_DIA in [i.rotulo for i in resultado.ausentes]


def test_itens_obrigatorios_disponivel_com_email_do_dia(tmp_path):
    (tmp_path / 'VEM BENEFICIOS').mkdir()
    _criar_arquivo(tmp_path / 'VEM BENEFICIOS', NOME_EXTRATO)
    (tmp_path / 'BPO').mkdir()
    _criar_arquivo(tmp_path / 'BPO', NOME_RETORNO_VALIDO)
    _criar_extrato_diario(tmp_path, DATA_REF, 'VEM BENEFICIOS.csv')

    itens = montar_itens_obrigatorios(
        DATA_REF,
        pasta_extrato=tmp_path / 'VEM BENEFICIOS',
        pasta_retorno=tmp_path / 'BPO',
        pasta_raiz=tmp_path,
    )
    resultado = validar_arquivos_obrigatorios(itens)

    assert resultado.disponivel is True
    # O item de gate não é consumido pela importação (só valida frescor).
    arquivos = montar_arquivos_importacao(resultado)
    assert arquivos.extrato_bancario.name == NOME_EXTRATO


# --------------------------------------------------------------------------- #
# parsear_data_referencia (pura)
# --------------------------------------------------------------------------- #
def test_parsear_data_sem_argumento_usa_hoje():
    assert parsear_data_referencia(()) == date.today()


def test_parsear_data_com_argumento_valido():
    assert parsear_data_referencia(('05-08-2026',)) == DATA_REF


def test_parsear_data_com_argumento_invalido():
    with pytest.raises(ValueError):
        parsear_data_referencia(('2026-08-05',))


# --------------------------------------------------------------------------- #
# _aguardar_sincronizacao (I/O web instrumentado) — melhoria 2
# --------------------------------------------------------------------------- #
def test_aguardar_sincronizacao_confirma_sucesso(monkeypatch):
    monkeypatch.setattr(
        iew,
        '_consultar_sinal_sincronizacao',
        lambda chave: {'ok': True, 'msg': 'Extrato sincronizado ✓'},
    )
    # Não deve levantar.
    iew._aguardar_sincronizacao('extrato', ROTULO_EXTRATO)


def test_aguardar_sincronizacao_falha_em_erro_do_painel(monkeypatch):
    monkeypatch.setattr(
        iew,
        '_consultar_sinal_sincronizacao',
        lambda chave: {'ok': False, 'msg': 'Erro ao sincronizar extrato'},
    )
    with pytest.raises(RuntimeError):
        iew._aguardar_sincronizacao('extrato', ROTULO_EXTRATO)


def test_aguardar_sincronizacao_timeout_sem_confirmacao(monkeypatch):
    monkeypatch.setattr(
        iew, '_consultar_sinal_sincronizacao', lambda chave: None
    )
    monkeypatch.setattr(iew, 'TIMEOUT_SINCRONIZACAO_SEG', 0.05)
    monkeypatch.setattr(iew, 'INTERVALO_POLL_SEG', 0.01)
    with pytest.raises(TimeoutError):
        iew._aguardar_sincronizacao('extrato', ROTULO_EXTRATO)


# --------------------------------------------------------------------------- #
# executar_importacao_automatica — idempotência (checkpoint) — melhoria 3
# --------------------------------------------------------------------------- #
def test_importacao_curto_circuito_quando_ja_concluida(monkeypatch):
    monkeypatch.setattr(iew, 'importacao_ja_concluida', lambda data: True)
    houve_import = {'chamou': False}

    def _falhar(*args, **kwargs):
        houve_import['chamou'] = True

    monkeypatch.setattr(iew, 'importar_no_painel', _falhar)

    resultado = iew.executar_importacao_automatica(DATA_REF)

    assert resultado is True
    assert houve_import['chamou'] is False  # não reimportou


def test_importacao_registra_checkpoint_apos_sucesso(monkeypatch):
    monkeypatch.setattr(iew, 'importacao_ja_concluida', lambda data: False)
    monkeypatch.setattr(
        iew,
        'montar_itens_obrigatorios',
        lambda data: (
            ItemObrigatorio(ROTULO_EXTRATO_DIA, Path('d.csv')),
            ItemObrigatorio(ROTULO_EXTRATO, Path('a.xlsx')),
            ItemObrigatorio(ROTULO_RETORNO, Path('b.csv')),
        ),
    )
    monkeypatch.setattr(
        iew, 'carregar_credenciais', lambda: iew.CredenciaisPainel('u', 's')
    )
    monkeypatch.setattr(iew, 'importar_no_painel', lambda *a, **k: None)
    monkeypatch.setattr(
        iew, 'notificar_arquivos_no_teams', lambda *a, **k: True
    )
    registrado = {}
    monkeypatch.setattr(
        iew,
        'registrar_importacao_concluida',
        lambda data: registrado.setdefault('data', data),
    )

    assert iew.executar_importacao_automatica(DATA_REF) is True
    assert registrado['data'] == DATA_REF


# --------------------------------------------------------------------------- #
# notificar_arquivos_no_teams — etapa anterior à importação
# --------------------------------------------------------------------------- #
def _arquivos_fake() -> iew.ArquivosImportacao:
    return iew.ArquivosImportacao(
        extrato_bancario=Path('a.xlsx'), retorno_bpo=Path('b.csv')
    )


def test_teams_envia_os_dois_arquivos_da_importacao(monkeypatch):
    monkeypatch.setattr(iew, 'envio_teams_ja_concluido', lambda data: False)
    monkeypatch.setattr(
        iew, 'registrar_envio_teams_concluido', lambda data: None
    )
    enviados = {}

    def _enviar(caminhos, data_referencia):
        enviados['caminhos'] = tuple(caminhos)
        enviados['data'] = data_referencia
        return True

    monkeypatch.setattr(iew, 'enviar_arquivos_no_chat', _enviar)

    assert iew.notificar_arquivos_no_teams(_arquivos_fake(), DATA_REF) is True
    assert enviados['caminhos'] == (Path('a.xlsx'), Path('b.csv'))
    assert enviados['data'] == DATA_REF


def test_teams_nao_republica_no_mesmo_dia(monkeypatch):
    monkeypatch.setattr(iew, 'envio_teams_ja_concluido', lambda data: True)
    chamou = {'envio': False}

    def _falhar(*args, **kwargs):
        chamou['envio'] = True
        return True

    monkeypatch.setattr(iew, 'enviar_arquivos_no_chat', _falhar)

    assert iew.notificar_arquivos_no_teams(_arquivos_fake(), DATA_REF) is True
    assert chamou['envio'] is False


def test_teams_falha_nao_interrompe_a_importacao(monkeypatch):
    monkeypatch.setattr(iew, 'envio_teams_ja_concluido', lambda data: False)

    def _explodir(*args, **kwargs):
        raise iew.ErroEnvioTeams('chat indisponível')

    monkeypatch.setattr(iew, 'enviar_arquivos_no_chat', _explodir)
    monkeypatch.setattr(iew, 'importacao_ja_concluida', lambda data: False)
    monkeypatch.setattr(
        iew,
        'montar_itens_obrigatorios',
        lambda data: (
            ItemObrigatorio(ROTULO_EXTRATO_DIA, Path('d.csv')),
            ItemObrigatorio(ROTULO_EXTRATO, Path('a.xlsx')),
            ItemObrigatorio(ROTULO_RETORNO, Path('b.csv')),
        ),
    )
    monkeypatch.setattr(
        iew, 'carregar_credenciais', lambda: iew.CredenciaisPainel('u', 's')
    )
    importou = {'chamou': False}
    monkeypatch.setattr(
        iew,
        'importar_no_painel',
        lambda *a, **k: importou.update(chamou=True),
    )
    monkeypatch.setattr(
        iew, 'registrar_importacao_concluida', lambda data: None
    )

    assert iew.executar_importacao_automatica(DATA_REF) is True
    assert importou['chamou'] is True
