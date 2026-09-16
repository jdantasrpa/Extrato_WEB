# INSERIR EM: automação_email_antigo/tests/test_notificacao_teams.py
# DEPENDÊNCIA: pip install pytest

# --- stdlib ---
from datetime import date
from pathlib import Path

# --- terceiros ---
import pytest

# --- locais ---
import notificacao_teams as nt
from notificacao_teams import (
    AnexoChat,
    ConfiguracaoTeams,
    ErroEnvioTeams,
    carregar_configuracao,
    enviar_arquivos_no_chat,
    extrair_id_anexo,
    formatar_mensagem,
    interpretar_booleano,
    montar_anexo,
    montar_corpo_mensagem,
    montar_nome_remoto,
    normalizar_topico,
    topico_casa,
)

DATA_REF = date(2026, 8, 13)
GUID = '0b2d3f4e-1a2b-4c3d-8e9f-1a2b3c4d5e6f'


# --------------------------------------------------------------------------- #
# normalizar_topico / topico_casa (puras)
# --------------------------------------------------------------------------- #
def test_normalizar_remove_acento_caixa_e_espacos():
    assert normalizar_topico('  Extrato   Bancário ') == 'extrato bancario'


def test_topico_casa_ignora_acento_e_caixa():
    assert topico_casa('Extrato Bancário', 'Extrato bancario') is True


def test_topico_casa_aceita_nome_parcial():
    assert topico_casa('Extrato bancario - Alvo', 'Extrato bancario') is True


def test_topico_casa_falso_para_outro_chat():
    assert topico_casa('Cobrança Atividades Diárias', 'Extrato bancario') is (
        False
    )


def test_topico_casa_falso_para_topico_vazio():
    assert topico_casa('', 'Extrato bancario') is False


# --------------------------------------------------------------------------- #
# extrair_id_anexo / montar_anexo (puras)
# --------------------------------------------------------------------------- #
def test_extrair_id_usa_guid_do_etag():
    item = {'eTag': f'"{{{GUID}}},1"', 'id': '01ITEMID'}
    assert extrair_id_anexo(item) == GUID


def test_extrair_id_cai_para_id_do_item_sem_etag():
    assert extrair_id_anexo({'id': '01ITEMID'}) == '01ITEMID'


def test_montar_anexo_prefere_link_de_compartilhamento():
    item = {'eTag': f'"{{{GUID}}},1"', 'name': 'a.csv', 'webUrl': 'http://w'}
    anexo = montar_anexo(item, 'http://compartilhado')

    assert anexo == AnexoChat(GUID, 'a.csv', 'http://compartilhado')


def test_montar_anexo_usa_weburl_sem_link():
    item = {'id': '01X', 'name': 'a.csv', 'webUrl': 'http://w'}
    assert montar_anexo(item).url == 'http://w'


# --------------------------------------------------------------------------- #
# montar_nome_remoto / formatar_mensagem / montar_corpo_mensagem (puras)
# --------------------------------------------------------------------------- #
def test_montar_nome_remoto_prefixa_data():
    assert montar_nome_remoto('base.xlsx', DATA_REF) == '13-08-2026_base.xlsx'


def test_formatar_mensagem_lista_arquivos_e_data():
    mensagem = formatar_mensagem(DATA_REF, ('a.xlsx', 'b.csv'))

    assert '13/08/2026' in mensagem
    assert '<li>a.xlsx</li>' in mensagem
    assert '<li>b.csv</li>' in mensagem


def test_montar_corpo_inclui_marcador_por_anexo():
    anexos = (AnexoChat('id-1', 'a.csv', 'http://a'),)
    corpo = montar_corpo_mensagem('<p>oi</p>', anexos)

    assert '<attachment id="id-1"></attachment>' in corpo['body']['content']
    assert corpo['body']['contentType'] == 'html'
    assert corpo['attachments'] == [
        {
            'id': 'id-1',
            'contentType': 'reference',
            'contentUrl': 'http://a',
            'name': 'a.csv',
        }
    ]


# --------------------------------------------------------------------------- #
# interpretar_booleano / carregar_configuracao
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize('valor', ['sim', 'S', '1', 'true', 'YES'])
def test_interpretar_booleano_verdadeiro(valor):
    assert interpretar_booleano(valor) is True


@pytest.mark.parametrize('valor', ['nao', 'não', '0', 'false'])
def test_interpretar_booleano_falso(valor):
    assert interpretar_booleano(valor) is False


def test_interpretar_booleano_ausente_usa_padrao():
    assert interpretar_booleano(None) is True
    assert interpretar_booleano('   ', padrao=False) is False


def test_carregar_configuracao_le_bloco_teams(tmp_path, monkeypatch):
    monkeypatch.delenv(nt.ENV_GRUPO, raising=False)
    monkeypatch.delenv(nt.ENV_ATIVO, raising=False)
    caminho = tmp_path / 'config.ini'
    caminho.write_text(
        '[TEAMS]\ngrupo = Extrato bancario\nativo = nao\n', encoding='utf-8'
    )

    configuracao = carregar_configuracao(caminho)

    assert configuracao == ConfiguracaoTeams('Extrato bancario', False)


def test_carregar_configuracao_env_tem_prioridade(tmp_path, monkeypatch):
    caminho = tmp_path / 'config.ini'
    caminho.write_text('[TEAMS]\ngrupo = Outro\n', encoding='utf-8')
    monkeypatch.setenv(nt.ENV_GRUPO, 'Extrato bancario')
    monkeypatch.setenv(nt.ENV_ATIVO, 'sim')

    assert carregar_configuracao(caminho).grupo == 'Extrato bancario'


def test_carregar_configuracao_sem_arquivo_usa_padrao(tmp_path, monkeypatch):
    monkeypatch.delenv(nt.ENV_GRUPO, raising=False)
    monkeypatch.delenv(nt.ENV_ATIVO, raising=False)

    configuracao = carregar_configuracao(tmp_path / 'inexistente.ini')

    assert configuracao == ConfiguracaoTeams(nt.GRUPO_PADRAO, True)


# --------------------------------------------------------------------------- #
# enviar_arquivos_no_chat (orquestração, com Graph simulado)
# --------------------------------------------------------------------------- #
@pytest.fixture
def graph_simulado(monkeypatch):
    """Substitui as chamadas de rede e registra o que seria enviado."""
    registro: dict = {'subidos': [], 'corpo': None, 'chat': None}

    monkeypatch.setattr(nt, 'obter_token', lambda *a, **k: 'token')
    monkeypatch.setattr(nt, 'buscar_id_chat', lambda token, nome: 'chat-1')
    monkeypatch.setattr(
        nt, 'criar_link_organizacao', lambda token, item: 'http://link'
    )

    def _subir(token, caminho, nome_remoto):
        registro['subidos'].append(nome_remoto)
        return {
            'id': f'item-{len(registro["subidos"])}',
            'name': nome_remoto,
            'webUrl': 'http://w',
        }

    def _publicar(token, id_chat, corpo):
        registro['chat'] = id_chat
        registro['corpo'] = corpo

    monkeypatch.setattr(nt, 'subir_arquivo', _subir)
    monkeypatch.setattr(nt, 'publicar_mensagem', _publicar)
    return registro


def _criar_arquivo(pasta: Path, nome: str) -> Path:
    caminho = pasta / nome
    caminho.write_text('conteudo', encoding='utf-8')
    return caminho


def test_enviar_publica_todos_os_arquivos(tmp_path, graph_simulado):
    arquivos = (
        _criar_arquivo(tmp_path, 'consolidado.xlsx'),
        _criar_arquivo(tmp_path, 'retorno.csv'),
    )

    enviado = enviar_arquivos_no_chat(
        arquivos, DATA_REF, ConfiguracaoTeams('Extrato bancario', True)
    )

    assert enviado is True
    assert graph_simulado['subidos'] == [
        '13-08-2026_consolidado.xlsx',
        '13-08-2026_retorno.csv',
    ]
    assert graph_simulado['chat'] == 'chat-1'
    assert len(graph_simulado['corpo']['attachments']) == 2
    assert graph_simulado['corpo']['attachments'][0]['contentUrl'] == (
        'http://link'
    )


def test_enviar_desativado_nao_chama_graph(tmp_path, graph_simulado):
    arquivos = (_criar_arquivo(tmp_path, 'consolidado.xlsx'),)

    enviado = enviar_arquivos_no_chat(
        arquivos, DATA_REF, ConfiguracaoTeams('Extrato bancario', False)
    )

    assert enviado is False
    assert graph_simulado['subidos'] == []


def test_enviar_sem_arquivo_existente_nao_publica(tmp_path, graph_simulado):
    enviado = enviar_arquivos_no_chat(
        (tmp_path / 'nao_existe.csv',),
        DATA_REF,
        ConfiguracaoTeams('Extrato bancario', True),
    )

    assert enviado is False
    assert graph_simulado['corpo'] is None


def test_enviar_sem_chat_encontrado_levanta_erro(
    tmp_path, graph_simulado, monkeypatch
):
    monkeypatch.setattr(nt, 'buscar_id_chat', lambda token, nome: '')
    arquivos = (_criar_arquivo(tmp_path, 'consolidado.xlsx'),)

    with pytest.raises(ErroEnvioTeams, match='não encontrado'):
        enviar_arquivos_no_chat(
            arquivos, DATA_REF, ConfiguracaoTeams('Extrato bancario', True)
        )
