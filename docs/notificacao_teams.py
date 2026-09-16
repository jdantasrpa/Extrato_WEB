# INSERIR EM: automação_email_antigo/notificacao_teams.py
# DEPENDÊNCIA: pip install msal requests
"""Envio dos insumos diários em um chat de grupo do Teams (Graph API).

Antes de anexar os arquivos no painel Extrato_WEB, os mesmos arquivos são
publicados no chat de grupo do Teams para que o time consuma o dado na
origem — sem depender do painel.

Fluxo no Microsoft Graph (mesma estrutura dos robôs
``fiducial_datacob_contratos_ativos`` e
``bpo_processamento_convenios_competencia``):

1. ``msal`` device code flow, com cache de token em disco fora do repo e
   semeado a partir do cache já autenticado de outro robô da Alvo Card;
2. ``GET /me/chats``                    -> localiza o chat pelo ``topic``;
3. ``PUT /me/drive/root:/...:/content`` -> sobe o arquivo na pasta padrão
   "Microsoft Teams Chat Files" do OneDrive da conta do robô;
4. ``POST /me/drive/items/{id}/createLink`` -> libera leitura para a
   organização (sem isso, os membros do chat abrem o anexo e recebem
   "acesso negado");
5. ``POST /me/chats/{id}/messages``     -> publica a mensagem com os
   anexos referenciados.

Separação obrigatória: as funções puras (montagem de payload, nomes e
comparação de tópico) ficam no topo e são testáveis sem rede; o I/O de
Graph fica isolado nas funções do bloco seguinte.
"""

# --- stdlib ---
import configparser
import logging
import os
import re
import unicodedata
import webbrowser
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Optional, Sequence
from urllib.parse import quote

# --- terceiros ---
import msal
import requests

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Constantes — autenticação
# --------------------------------------------------------------------------- #
GRAPH_CLIENT_ID = '14d82eec-204b-4c2f-b7e8-296a70dab67e'
GRAPH_AUTHORITY = 'https://login.microsoftonline.com/common'

# Escopos consentíveis pelo próprio usuário no app 'Microsoft Graph
# Command Line Tools'. Files.ReadWrite é exigido pelo upload do anexo:
# no Teams, arquivo de chat vive no OneDrive de quem envia.
GRAPH_SCOPES = (
    'Chat.ReadBasic',
    'ChatMessage.Send',
    'Files.ReadWrite',
)

PASTA_CACHE = (
    Path(os.environ.get('LOCALAPPDATA', Path.home())) / 'rpa_alvocard'
)
CAMINHO_CACHE_TOKEN = PASTA_CACHE / 'token_cache_email.json'
CAMINHOS_CACHE_SEMENTE = (
    Path(r'C:\RPA\fiducial_datacob_contratos_ativos\.token_cache_email.json'),
)

# --------------------------------------------------------------------------- #
# Constantes — Graph
# --------------------------------------------------------------------------- #
GRAPH_URL_CHATS = 'https://graph.microsoft.com/v1.0/me/chats'
GRAPH_URL_MENSAGENS = 'https://graph.microsoft.com/v1.0/me/chats/%s/messages'
GRAPH_URL_DRIVE_RAIZ = 'https://graph.microsoft.com/v1.0/me/drive/root'
GRAPH_URL_LINK = (
    'https://graph.microsoft.com/v1.0/me/drive/items/%s/createLink'
)

PASTA_ONEDRIVE_CHAT = 'Microsoft Teams Chat Files'
TAMANHO_PAGINA_CHATS = 50
TIMEOUT_PADRAO = 60
TIMEOUT_UPLOAD = 300

# Acima de 4 MB o Graph exige sessão de upload em blocos; o bloco precisa
# ser múltiplo de 320 KiB.
LIMITE_UPLOAD_SIMPLES_BYTES = 4 * 1024 * 1024
TAMANHO_BLOCO_BYTES = 10 * 320 * 1024

PADRAO_GUID = re.compile(
    r'[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-'
    r'[0-9a-fA-F]{4}-[0-9a-fA-F]{12}'
)

# --------------------------------------------------------------------------- #
# Constantes — configuração local
# --------------------------------------------------------------------------- #
CAMINHO_CONFIG = Path(__file__).with_name('config.ini')
BLOCO_CONFIG_TEAMS = 'TEAMS'
ENV_GRUPO = 'ALVO_TEAMS_GRUPO'
ENV_ATIVO = 'ALVO_TEAMS_ATIVO'

# ``topic`` do chat de grupo que consome extrato + retorno BPO.
GRUPO_PADRAO = 'Extrato bancario'
VALORES_VERDADEIROS = ('1', 'sim', 's', 'true', 'yes')


class ErroEnvioTeams(RuntimeError):
    """Falha ao publicar os insumos no chat de grupo do Teams."""


@dataclass(frozen=True)
class ConfiguracaoTeams:
    """Parâmetros de envio ao chat de grupo do Teams."""

    grupo: str
    ativo: bool


@dataclass(frozen=True)
class AnexoChat:
    """Anexo já hospedado no OneDrive, pronto para citar na mensagem."""

    identificador: str
    nome: str
    url: str


# --------------------------------------------------------------------------- #
# Funções puras — normalização e montagem de payload
# --------------------------------------------------------------------------- #
def normalizar_topico(texto: str) -> str:
    """Normaliza um tópico de chat para comparação tolerante.

    Remove acentos, colapsa espaços e ignora a caixa. Evita que o chat
    deixe de ser encontrado por variação de grafia entre o config.ini
    ('Extrato bancario') e o nome real no Teams ('Extrato Bancário').

    Args:
        texto: Tópico bruto do chat ou nome procurado.

    Returns:
        Texto normalizado, em minúsculas e sem acentos.

    Example:
        >>> normalizar_topico('  Extrato   Bancário ')
        'extrato bancario'
    """
    sem_acento = unicodedata.normalize('NFKD', texto or '')
    sem_acento = ''.join(c for c in sem_acento if not unicodedata.combining(c))
    return ' '.join(sem_acento.split()).casefold()


def topico_casa(topico: str, nome: str) -> bool:
    """Indica se o ``topic`` do chat corresponde ao nome procurado.

    Args:
        topico: ``topic`` do chat retornado pelo Graph.
        nome: Nome procurado do chat de grupo.

    Returns:
        True se ``nome`` estiver contido em ``topico``.

    Example:
        >>> topico_casa('Extrato Bancário', 'extrato bancario')
        True
    """
    topico_limpo = normalizar_topico(topico)
    nome_limpo = normalizar_topico(nome)
    return (
        bool(topico_limpo) and bool(nome_limpo) and nome_limpo in topico_limpo
    )


def extrair_id_anexo(item: dict) -> str:
    """Extrai o identificador do anexo a partir do driveItem do Graph.

    O Teams espera o GUID do ``eTag`` do arquivo como id do anexo; quando
    o eTag não vier, o id do próprio item é aceito.

    Args:
        item: driveItem devolvido pelo upload.

    Returns:
        Identificador a usar no anexo da mensagem.

    Example:
        >>> extrair_id_anexo({'eTag': '"{0B2D-x}"', 'id': '01ABC'})
        '01ABC'
    """
    encontrado = PADRAO_GUID.search(item.get('eTag') or '')
    return encontrado.group(0) if encontrado else (item.get('id') or '')


def montar_anexo(item: dict, url_alternativa: str = '') -> AnexoChat:
    """Converte o driveItem do upload em um anexo de mensagem de chat.

    Args:
        item: driveItem devolvido pelo Graph.
        url_alternativa: Link de compartilhamento a usar no lugar do
            ``webUrl`` do item, quando disponível.

    Returns:
        AnexoChat com identificador, nome e URL do arquivo.
    """
    return AnexoChat(
        identificador=extrair_id_anexo(item),
        nome=item.get('name') or '',
        url=url_alternativa or item.get('webUrl') or '',
    )


def montar_nome_remoto(nome_arquivo: str, data_referencia: date) -> str:
    """Prefixa o nome do arquivo com a data de referência.

    Mantém um item por dia no OneDrive: reexecuções da mesma data
    substituem o mesmo arquivo (idempotência), e dias diferentes não
    invalidam o link já publicado no chat.

    Args:
        nome_arquivo: Nome original do arquivo em disco.
        data_referencia: Data de referência da execução.

    Returns:
        Nome do arquivo no OneDrive.

    Example:
        >>> from datetime import date
        >>> montar_nome_remoto('base.xlsx', date(2026, 8, 13))
        '13-08-2026_base.xlsx'
    """
    return f"{data_referencia.strftime('%d-%m-%Y')}_{nome_arquivo}"


def formatar_mensagem(
    data_referencia: date,
    nomes_arquivos: Sequence[str],
) -> str:
    """Monta o texto HTML que acompanha os anexos no chat.

    Args:
        data_referencia: Data de referência dos insumos.
        nomes_arquivos: Nomes dos arquivos publicados.

    Returns:
        Trecho HTML com o cabeçalho e a lista de arquivos.

    Example:
        >>> from datetime import date
        >>> '13/08/2026' in formatar_mensagem(date(2026, 8, 13), ('a.csv',))
        True
    """
    itens = ''.join(f'<li>{nome}</li>' for nome in nomes_arquivos)
    return (
        f'<p><b>\U0001f4ce Extrato bancário — '
        f"{data_referencia.strftime('%d/%m/%Y')}</b></p>"
        '<p>Arquivos utilizados na importação do painel Extrato_WEB:</p>'
        f'<ul>{itens}</ul>'
    )


def montar_corpo_mensagem(
    conteudo_html: str,
    anexos: Sequence[AnexoChat],
) -> dict:
    """Monta o payload da mensagem de chat com anexos referenciados.

    Args:
        conteudo_html: Texto HTML que antecede os anexos.
        anexos: Anexos já hospedados no OneDrive.

    Returns:
        Corpo JSON aceito por ``POST /me/chats/{id}/messages``.

    Example:
        >>> anexo = AnexoChat('id-1', 'a.csv', 'http://x/a.csv')
        >>> corpo = montar_corpo_mensagem('<p>oi</p>', (anexo,))
        >>> corpo['attachments'][0]['contentType']
        'reference'
    """
    marcadores = ''.join(
        f'<attachment id="{anexo.identificador}"></attachment>'
        for anexo in anexos
    )
    return {
        'body': {
            'contentType': 'html',
            'content': f'{conteudo_html}{marcadores}',
        },
        'attachments': [
            {
                'id': anexo.identificador,
                'contentType': 'reference',
                'contentUrl': anexo.url,
                'name': anexo.nome,
            }
            for anexo in anexos
        ],
    }


def interpretar_booleano(valor: Optional[str], padrao: bool = True) -> bool:
    """Interpreta um valor textual de configuração como booleano.

    Args:
        valor: Texto lido do INI ou da variável de ambiente.
        padrao: Valor devolvido quando o texto está vazio ou ausente.

    Returns:
        True quando o texto representa afirmação.

    Example:
        >>> interpretar_booleano('nao')
        False
    """
    if valor is None or not valor.strip():
        return padrao
    return valor.strip().casefold() in VALORES_VERDADEIROS


# --------------------------------------------------------------------------- #
# I/O — configuração local
# --------------------------------------------------------------------------- #
def carregar_configuracao(
    caminho_config: Path = CAMINHO_CONFIG,
) -> ConfiguracaoTeams:
    """Carrega o chat de destino via ENV (prioridade) ou config.ini.

    Bloco esperado no ``config.ini``::

        [TEAMS]
        grupo = Extrato bancario
        ativo = sim

    Args:
        caminho_config: Caminho do arquivo INI de configuração.

    Returns:
        ConfiguracaoTeams com o tópico do chat e o liga/desliga da etapa.
    """
    grupo = os.environ.get(ENV_GRUPO)
    ativo_bruto = os.environ.get(ENV_ATIVO)

    if (not grupo or ativo_bruto is None) and caminho_config.is_file():
        parser = configparser.ConfigParser()
        parser.read(caminho_config, encoding='utf-8')
        if parser.has_section(BLOCO_CONFIG_TEAMS):
            bloco = parser[BLOCO_CONFIG_TEAMS]
            grupo = grupo or bloco.get('grupo')
            if ativo_bruto is None:
                ativo_bruto = bloco.get('ativo')

    return ConfiguracaoTeams(
        grupo=grupo or GRUPO_PADRAO,
        ativo=interpretar_booleano(ativo_bruto),
    )


# --------------------------------------------------------------------------- #
# I/O — autenticação (MSAL)
# --------------------------------------------------------------------------- #
def _carregar_cache() -> msal.SerializableTokenCache:
    """Carrega o cache de token, semeando de outro robô se necessário."""
    cache = msal.SerializableTokenCache()

    for origem in (CAMINHO_CACHE_TOKEN,) + CAMINHOS_CACHE_SEMENTE:
        if origem.exists():
            cache.deserialize(origem.read_text(encoding='utf-8'))
            logger.info('Cache de token carregado de: %s', origem)
            break

    return cache


def _salvar_cache(cache: msal.SerializableTokenCache) -> None:
    """Persiste o cache de token fora do repositório."""
    if not cache.has_state_changed:
        return

    PASTA_CACHE.mkdir(parents=True, exist_ok=True)
    CAMINHO_CACHE_TOKEN.write_text(cache.serialize(), encoding='utf-8')


def _autenticar_device_flow(
    app: msal.PublicClientApplication,
    cache: msal.SerializableTokenCache,
    scopes: list,
) -> str:
    """Executa o device code flow e devolve o access token."""
    flow = app.initiate_device_flow(scopes=scopes)

    if 'user_code' not in flow:
        raise ErroEnvioTeams(f'Falha ao iniciar device flow: {flow}')

    logger.warning(
        'AUTENTICACAO NECESSARIA - Codigo: %s | URL: %s',
        flow['user_code'],
        flow['verification_uri'],
    )
    webbrowser.open(flow['verification_uri'])

    resultado = app.acquire_token_by_device_flow(flow)
    if 'access_token' not in resultado:
        raise ErroEnvioTeams(
            f"Falha na autenticacao: {resultado.get('error_description')}"
        )

    _salvar_cache(cache)
    return resultado['access_token']


def obter_token(
    permitir_interativo: bool = True,
    scopes: tuple = GRAPH_SCOPES,
) -> str:
    """Obtém token do Graph, silenciosamente sempre que possível.

    Args:
        permitir_interativo: Autoriza device code flow quando o token
            silencioso não estiver disponível.
        scopes: Escopos do Graph a solicitar.

    Returns:
        Access token válido para o Microsoft Graph.

    Raises:
        ErroEnvioTeams: Se não for possível autenticar.
    """
    lista_scopes = list(scopes)
    cache = _carregar_cache()
    app = msal.PublicClientApplication(
        GRAPH_CLIENT_ID, authority=GRAPH_AUTHORITY, token_cache=cache
    )

    contas = app.get_accounts()
    if contas:
        resultado = app.acquire_token_silent(lista_scopes, account=contas[0])
        if resultado and 'access_token' in resultado:
            _salvar_cache(cache)
            return resultado['access_token']

    if not permitir_interativo:
        raise ErroEnvioTeams(
            'Token silencioso indisponível e modo interativo desabilitado.'
        )

    return _autenticar_device_flow(app, cache, lista_scopes)


# --------------------------------------------------------------------------- #
# I/O — Microsoft Graph
# --------------------------------------------------------------------------- #
def _cabecalho(token: str) -> dict:
    """Monta o cabeçalho de autorização do Graph."""
    return {'Authorization': f'Bearer {token}'}


def buscar_id_chat(token: str, nome: str) -> str:
    """Busca o id do chat de grupo pelo ``topic``, tratando paginação.

    Args:
        token: Access token com escopo de leitura de chat.
        nome: ``topic`` do chat de grupo no Teams.

    Returns:
        Id do primeiro chat cujo ``topic`` casa com o nome, ou vazio.

    Raises:
        requests.RequestException: Falha de rede/HTTP na consulta.
    """
    url = GRAPH_URL_CHATS
    parametros: Optional[dict] = {
        '$select': 'id,topic,chatType',
        '$top': str(TAMANHO_PAGINA_CHATS),
    }

    while url:
        resposta = requests.get(
            url,
            headers=_cabecalho(token),
            params=parametros,
            timeout=TIMEOUT_PADRAO,
        )
        resposta.raise_for_status()
        dados = resposta.json()

        for chat in dados.get('value', []):
            if topico_casa(chat.get('topic', ''), nome):
                return chat.get('id', '')

        url = dados.get('@odata.nextLink')
        parametros = None

    return ''


def _url_conteudo(nome_remoto: str, sufixo: str) -> str:
    """Monta a URL do driveItem na pasta de arquivos de chat."""
    caminho = quote(f'{PASTA_ONEDRIVE_CHAT}/{nome_remoto}')
    return f'{GRAPH_URL_DRIVE_RAIZ}:/{caminho}:/{sufixo}'


def _subir_arquivo_pequeno(
    token: str, caminho: Path, nome_remoto: str
) -> dict:
    """Sobe arquivo de até 4 MB em uma única requisição."""
    resposta = requests.put(
        _url_conteudo(nome_remoto, 'content'),
        headers=_cabecalho(token),
        params={'@microsoft.graph.conflictBehavior': 'replace'},
        data=caminho.read_bytes(),
        timeout=TIMEOUT_UPLOAD,
    )
    resposta.raise_for_status()
    return resposta.json()


def _subir_arquivo_grande(token: str, caminho: Path, nome_remoto: str) -> dict:
    """Sobe arquivo acima de 4 MB em blocos, via sessão de upload."""
    resposta = requests.post(
        _url_conteudo(nome_remoto, 'createUploadSession'),
        headers=_cabecalho(token),
        json={'item': {'@microsoft.graph.conflictBehavior': 'replace'}},
        timeout=TIMEOUT_PADRAO,
    )
    resposta.raise_for_status()
    url_upload = resposta.json()['uploadUrl']

    tamanho = caminho.stat().st_size
    inicio = 0
    with caminho.open('rb') as arquivo:
        while inicio < tamanho:
            bloco = arquivo.read(TAMANHO_BLOCO_BYTES)
            fim = inicio + len(bloco) - 1
            parcial = requests.put(
                url_upload,
                headers={'Content-Range': f'bytes {inicio}-{fim}/{tamanho}'},
                data=bloco,
                timeout=TIMEOUT_UPLOAD,
            )
            parcial.raise_for_status()
            inicio = fim + 1
            if parcial.status_code in (200, 201):
                return parcial.json()

    raise ErroEnvioTeams(f'Upload em blocos não concluído: {caminho.name}')


def subir_arquivo(token: str, caminho: Path, nome_remoto: str) -> dict:
    """Sobe o arquivo na pasta de arquivos de chat do OneDrive.

    Args:
        token: Access token com escopo ``Files.ReadWrite``.
        caminho: Arquivo local a publicar.
        nome_remoto: Nome do arquivo no OneDrive.

    Returns:
        driveItem devolvido pelo Graph.

    Raises:
        ErroEnvioTeams: Se o upload em blocos não concluir.
        requests.RequestException: Falha de rede/HTTP no upload.
    """
    logger.info('Subindo arquivo para o OneDrive: %s', nome_remoto)
    if caminho.stat().st_size <= LIMITE_UPLOAD_SIMPLES_BYTES:
        return _subir_arquivo_pequeno(token, caminho, nome_remoto)
    return _subir_arquivo_grande(token, caminho, nome_remoto)


def criar_link_organizacao(token: str, id_item: str) -> str:
    """Libera leitura do arquivo para a organização e devolve o link.

    Sem esse link, o anexo aparece no chat mas os membros recebem "acesso
    negado" — o arquivo nasce privado no OneDrive da conta do robô.

    Args:
        token: Access token com escopo ``Files.ReadWrite``.
        id_item: Id do driveItem recém-enviado.

    Returns:
        URL de compartilhamento, ou vazio se o Graph recusar (a mensagem
        segue com o ``webUrl`` do item).
    """
    try:
        resposta = requests.post(
            GRAPH_URL_LINK % id_item,
            headers=_cabecalho(token),
            json={'type': 'view', 'scope': 'organization'},
            timeout=TIMEOUT_PADRAO,
        )
        resposta.raise_for_status()
        return resposta.json().get('link', {}).get('webUrl', '')
    except requests.RequestException as exc:
        logger.warning('Não foi possível criar link de acesso: %s', exc)
        return ''


def publicar_mensagem(token: str, id_chat: str, corpo: dict) -> None:
    """Publica a mensagem com anexos no chat de grupo.

    Args:
        token: Access token com escopo ``ChatMessage.Send``.
        id_chat: Id do chat de grupo.
        corpo: Payload montado por ``montar_corpo_mensagem``.

    Raises:
        requests.RequestException: Falha de rede/HTTP na publicação.
    """
    resposta = requests.post(
        GRAPH_URL_MENSAGENS % id_chat,
        headers={**_cabecalho(token), 'Content-Type': 'application/json'},
        json=corpo,
        timeout=TIMEOUT_PADRAO,
    )
    resposta.raise_for_status()


# --------------------------------------------------------------------------- #
# Orquestração
# --------------------------------------------------------------------------- #
def _publicar_anexo(
    token: str, caminho: Path, data_referencia: date
) -> AnexoChat:
    """Sobe um arquivo e devolve o anexo pronto para a mensagem."""
    nome_remoto = montar_nome_remoto(caminho.name, data_referencia)
    item = subir_arquivo(token, caminho, nome_remoto)
    link = criar_link_organizacao(token, item.get('id', ''))
    return montar_anexo(item, link)


def enviar_arquivos_no_chat(
    caminhos: Sequence[Path],
    data_referencia: date,
    configuracao: Optional[ConfiguracaoTeams] = None,
) -> bool:
    """Publica os insumos do dia no chat de grupo do Teams.

    Args:
        caminhos: Arquivos a publicar (os mesmos anexados no painel).
        data_referencia: Data de referência dos insumos.
        configuracao: Configuração do chat; carregada do INI se omitida.

    Returns:
        True se a mensagem foi publicada; False se a etapa está desligada
        ou não há arquivos a enviar.

    Raises:
        ErroEnvioTeams: Se o chat não for encontrado ou o Graph recusar.
    """
    configuracao = configuracao or carregar_configuracao()
    if not configuracao.ativo:
        logger.info('Envio ao Teams desativado por configuração.')
        return False

    existentes = [Path(c) for c in caminhos if Path(c).is_file()]
    if not existentes:
        logger.warning('Nenhum arquivo existente para enviar ao Teams.')
        return False

    try:
        token = obter_token()
        id_chat = buscar_id_chat(token, configuracao.grupo)
        if not id_chat:
            raise ErroEnvioTeams(
                f'Chat de grupo "{configuracao.grupo}" não encontrado '
                'nos chats da conta autenticada.'
            )

        anexos = tuple(
            _publicar_anexo(token, caminho, data_referencia)
            for caminho in existentes
        )
        corpo = montar_corpo_mensagem(
            formatar_mensagem(data_referencia, [a.nome for a in anexos]),
            anexos,
        )
        publicar_mensagem(token, id_chat, corpo)
    except requests.RequestException as exc:
        raise ErroEnvioTeams(f'Falha na comunicação com o Graph: {exc}')

    logger.info(
        '%s arquivo(s) publicado(s) no chat "%s".',
        len(anexos),
        configuracao.grupo,
    )
    return True
