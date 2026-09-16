# INSERIR EM: automação_email_antigo/importar_extrato_web.py
# DEPENDÊNCIA: pip install py-rpautom
#
# Importação automática dos insumos diários no painel Extrato_WEB.
#
# Fluxo:
#   1. Valida a existência dos arquivos obrigatórios (gate único).
#   2. Se algum faltar -> registra crítica e encerra sem importar.
#   3. Se todos existirem -> publica os mesmos arquivos no chat de grupo
#      do Teams (para consumo direto pelo time) e, em seguida, abre o
#      painel web, autentica, navega até a aba "Importar Dados" e envia
#      os dois arquivos.
#   4. Não move nada: os arquivos permanecem nas pastas de origem.
#
# O envio ao Teams é best-effort: falha ali é registrada em log e NÃO
# impede a importação no painel — o dado no site não pode ficar refém de
# uma notificação.

# --- stdlib ---
import configparser
import logging
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Optional, Sequence

# --- terceiros ---
from py_rpautom import web_utils

# --- locais ---
from checkpoint_importacao import (
    envio_teams_ja_concluido,
    importacao_ja_concluida,
    registrar_envio_teams_concluido,
    registrar_importacao_concluida,
)
from notificacao_teams import ErroEnvioTeams, enviar_arquivos_no_chat

# --------------------------------------------------------------------------- #
# Constantes
# --------------------------------------------------------------------------- #
URL_PAINEL_PRODUCAO = 'https://jdantasrpa.github.io/Extrato_WEB/'
ENV_URL = 'ALVO_PAINEL_URL'
ENV_NAVEGADOR_PATH = 'ALVO_NAVEGADOR_PATH'

PASTA_RAIZ = Path(r'C:\RPA\automação_email_antigo\extratos')
PASTA_EXTRATO_BANCARIO = PASTA_RAIZ / 'VEM BENEFICIOS'
PASTA_RETORNO_BPO = PASTA_RAIZ / 'BPO'

NOME_ARQUIVO_EXTRATO = 'consolidado_VEM BENEFICIOS.xlsx'
PADRAO_RETORNO_BPO = re.compile(
    r'^\d{8}_\d{6}_consolidacao_arquivo_retorno\.csv$',
    re.IGNORECASE,
)

# Sufixo do CSV diário gravado por extrair_extratos.py apenas quando o
# e-mail do Banco Arbi chega no dia (ex.: '05-08-2026_VEM BENEFICIOS.csv').
# É a prova de frescor do extrato: sem ele, nenhum e-mail Arbi do dia
# chegou e a importação deve abortar como falta de arquivos.
SUFIXO_EXTRATO_DIARIO = 'VEM BENEFICIOS.csv'

ROTULO_EXTRATO_DIA = 'E-mail Arbi do dia (VEM BENEFICIOS)'
ROTULO_EXTRATO = 'Extrato bancário (VEM BENEFICIOS)'
ROTULO_RETORNO = 'Arquivo Retorno BPO'

# Seletores CSS do painel Extrato_WEB (ver index.html / app.js).
SELETOR_LOGIN_SCREEN = '#login-screen'
SELETOR_LOGIN_USUARIO = '#login-user'
SELETOR_LOGIN_SENHA = '#login-pass'
SELETOR_LOGIN_SUBMIT = '#login-form button[type="submit"]'
SELETOR_NAV_IMPORTAR = 'button[data-page="importar"]'
SELETOR_INPUT_EXTRATO = '#input-extrato-vem-beneficios'
SELETOR_INPUT_RETORNO = '#input-retorno'
SELETOR_TOAST_CONTAINER = '#toast-container'

# Chaves dos slots monitorados em window.__rpaSync (ver monitor de sync).
CHAVE_SYNC_EXTRATO = 'extrato'
CHAVE_SYNC_RETORNO = 'retorno'

CAMINHO_CONFIG = Path(__file__).with_name('config.ini')
BLOCO_CONFIG_PAINEL = 'PAINEL'
ENV_USUARIO = 'ALVO_PAINEL_USUARIO'
ENV_SENHA = 'ALVO_PAINEL_SENHA'
USUARIO_PADRAO = 'Master'

# Espera pela confirmação de sincronização com o Supabase (rede). O painel
# grava no localStorage na hora, mas o upsert no Supabase é assíncrono e só
# confirma via toast; sem esperar, o navegador fecha antes do sync concluir.
TIMEOUT_SINCRONIZACAO_SEG = 60
INTERVALO_POLL_SEG = 0.5

# Caminhos candidatos do Chrome (inclui instalação por-usuário no
# LOCALAPPDATA, que o py_rpautom não localiza sozinho).
CAMINHOS_CHROME_CANDIDATOS = (
    Path(os.environ.get('LOCALAPPDATA', ''))
    / r'Google\Chrome\Application\chrome.exe',
    Path(os.environ.get('PROGRAMFILES', ''))
    / r'Google\Chrome\Application\chrome.exe',
    Path(os.environ.get('PROGRAMFILES(X86)', ''))
    / r'Google\Chrome\Application\chrome.exe',
)

CAMINHO_LOG = PASTA_RAIZ / 'log_importacao_web.txt'

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Dataclasses (imutáveis)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class ItemObrigatorio:
    """Insumo obrigatório e o caminho resolvido (None se ausente)."""

    rotulo: str
    caminho: Optional[Path]


@dataclass(frozen=True)
class ResultadoValidacao:
    """Separação entre insumos encontrados e ausentes."""

    encontrados: tuple[ItemObrigatorio, ...]
    ausentes: tuple[ItemObrigatorio, ...]

    @property
    def disponivel(self) -> bool:
        """True quando nenhum insumo obrigatório está ausente."""
        return not self.ausentes


@dataclass(frozen=True)
class ArquivosImportacao:
    """Caminhos garantidamente existentes usados na importação web."""

    extrato_bancario: Path
    retorno_bpo: Path


@dataclass(frozen=True)
class CredenciaisPainel:
    """Credenciais de acesso ao painel Extrato_WEB."""

    usuario: str
    senha: str


# --------------------------------------------------------------------------- #
# Funções puras — validação (sem I/O; operam sobre caminhos já resolvidos)
# --------------------------------------------------------------------------- #
def validar_arquivos_obrigatorios(
    itens: Sequence[ItemObrigatorio],
) -> ResultadoValidacao:
    """Classifica cada insumo obrigatório em encontrado ou ausente.

    Função pura: assume que ``caminho`` já foi resolvido pelos
    localizadores (``None`` significa não localizado em disco).

    Args:
        itens: Insumos obrigatórios com seus caminhos resolvidos.

    Returns:
        ResultadoValidacao com as tuplas de encontrados e ausentes.

    Example:
        >>> itens = (ItemObrigatorio('BPO', Path('x.csv')),)
        >>> validar_arquivos_obrigatorios(itens).disponivel
        True
    """
    encontrados = tuple(i for i in itens if i.caminho is not None)
    ausentes = tuple(i for i in itens if i.caminho is None)
    return ResultadoValidacao(encontrados=encontrados, ausentes=ausentes)


def montar_arquivos_importacao(
    resultado: ResultadoValidacao,
) -> ArquivosImportacao:
    """Extrai os caminhos validados no formato consumido pela importação.

    Args:
        resultado: Resultado de validação já confirmado como disponível.

    Returns:
        ArquivosImportacao com extrato bancário e retorno BPO.

    Raises:
        ValueError: Se algum insumo estiver ausente ou faltar rótulo.
    """
    if not resultado.disponivel:
        raise ValueError('Há insumos obrigatórios ausentes.')

    por_rotulo = {i.rotulo: i.caminho for i in resultado.encontrados}
    extrato = por_rotulo.get(ROTULO_EXTRATO)
    retorno = por_rotulo.get(ROTULO_RETORNO)

    if extrato is None or retorno is None:
        raise ValueError('Insumos esperados não presentes no resultado.')

    return ArquivosImportacao(extrato_bancario=extrato, retorno_bpo=retorno)


# --------------------------------------------------------------------------- #
# I/O — localização de arquivos em disco
# --------------------------------------------------------------------------- #
def localizar_extrato_bancario(pasta: Path) -> Optional[Path]:
    """Localiza o consolidado do extrato bancário (VEM BENEFICIOS).

    Args:
        pasta: Pasta do originador Vem Benefícios.

    Returns:
        Caminho do arquivo se existir e for um arquivo, senão None.
    """
    caminho = pasta / NOME_ARQUIVO_EXTRATO
    return caminho if caminho.is_file() else None


def localizar_retorno_bpo(pasta: Path) -> Optional[Path]:
    """Localiza o retorno BPO mais recente que casa com o padrão de nome.

    Args:
        pasta: Pasta onde o retorno do BPO é depositado.

    Returns:
        Caminho do arquivo mais recente que casa o padrão, senão None.
    """
    if not pasta.is_dir():
        return None

    candidatos = sorted(
        (
            arquivo
            for arquivo in pasta.glob('*.csv')
            if PADRAO_RETORNO_BPO.match(arquivo.name)
        ),
        key=lambda arquivo: arquivo.stat().st_mtime,
        reverse=True,
    )
    return candidatos[0] if candidatos else None


def localizar_extrato_diario_do_dia(
    data_referencia: date,
    pasta_raiz: Path = PASTA_RAIZ,
) -> Optional[Path]:
    """Localiza o CSV diário de VEM BENEFICIOS gravado na data informada.

    Esse CSV só é gravado por ``extrair_extratos.py`` quando o e-mail do
    Banco Arbi chega no dia. Sua ausência é o sinal de que nenhum extrato
    do dia chegou — diferente do consolidado acumulado, que persiste entre
    dias e não distingue dados frescos de defasados.

    Args:
        data_referencia: Data alvo (define a subpasta e o prefixo do nome).
        pasta_raiz: Raiz onde ``extrair_extratos.py`` grava as subpastas.

    Returns:
        Caminho do CSV diário do dia se existir, senão None.
    """
    pasta_dia = pasta_raiz / data_referencia.strftime('%Y-%m-%d')
    if not pasta_dia.is_dir():
        return None

    prefixo = data_referencia.strftime('%d-%m-%Y')
    sufixo = SUFIXO_EXTRATO_DIARIO.upper()
    candidatos = [
        arquivo
        for arquivo in pasta_dia.glob('*.csv')
        if arquivo.name.upper().startswith(prefixo)
        and arquivo.name.upper().endswith(sufixo)
    ]
    return candidatos[0] if candidatos else None


def montar_itens_obrigatorios(
    data_referencia: date,
    pasta_extrato: Path = PASTA_EXTRATO_BANCARIO,
    pasta_retorno: Path = PASTA_RETORNO_BPO,
    pasta_raiz: Path = PASTA_RAIZ,
) -> tuple[ItemObrigatorio, ...]:
    """Resolve em disco todos os insumos obrigatórios do processo.

    Inclui o gate de frescor (e-mail Arbi do dia): se o CSV diário de
    VEM BENEFICIOS não existir, o item fica ausente e a importação é
    abortada como falta de arquivos, evitando reimportar dados defasados.

    Args:
        data_referencia: Data usada no gate do e-mail do dia.
        pasta_extrato: Pasta do extrato bancário (VEM BENEFICIOS).
        pasta_retorno: Pasta do retorno do BPO.
        pasta_raiz: Raiz das subpastas diárias de extração.

    Returns:
        Tupla de ItemObrigatorio com os caminhos resolvidos (ou None).
    """
    return (
        ItemObrigatorio(
            ROTULO_EXTRATO_DIA,
            localizar_extrato_diario_do_dia(data_referencia, pasta_raiz),
        ),
        ItemObrigatorio(
            ROTULO_EXTRATO, localizar_extrato_bancario(pasta_extrato)
        ),
        ItemObrigatorio(ROTULO_RETORNO, localizar_retorno_bpo(pasta_retorno)),
    )


# --------------------------------------------------------------------------- #
# I/O — credenciais
# --------------------------------------------------------------------------- #
def carregar_credenciais(
    caminho_config: Path = CAMINHO_CONFIG,
) -> CredenciaisPainel:
    """Carrega credenciais do painel via ENV (prioridade) ou config.ini.

    Nunca hardcoda a senha. Procure ``config.ini`` com o bloco::

        [PAINEL]
        usuario = Master
        senha = ***

    ou defina as variáveis de ambiente ALVO_PAINEL_USUARIO /
    ALVO_PAINEL_SENHA.

    Args:
        caminho_config: Caminho do arquivo INI de configuração.

    Returns:
        CredenciaisPainel com usuário e senha.

    Raises:
        RuntimeError: Se a senha não for encontrada em nenhuma fonte.
    """
    usuario = os.environ.get(ENV_USUARIO)
    senha = os.environ.get(ENV_SENHA)

    if (not usuario or not senha) and caminho_config.is_file():
        parser = configparser.ConfigParser()
        parser.read(caminho_config, encoding='utf-8')
        if parser.has_section(BLOCO_CONFIG_PAINEL):
            bloco = parser[BLOCO_CONFIG_PAINEL]
            usuario = usuario or bloco.get('usuario')
            senha = senha or bloco.get('senha')

    usuario = usuario or USUARIO_PADRAO
    if not senha:
        raise RuntimeError(
            'Senha do painel não configurada. Defina ALVO_PAINEL_SENHA '
            f'ou o bloco [{BLOCO_CONFIG_PAINEL}] em {caminho_config}.'
        )
    return CredenciaisPainel(usuario=usuario, senha=senha)


# --------------------------------------------------------------------------- #
# I/O — automação web (py_rpautom / Selenium)
# --------------------------------------------------------------------------- #
def _configurar_upload_local() -> None:
    """Desativa o upload remoto do Selenium para envio de arquivos locais.

    Com o detector padrão, ``send_keys`` de um caminho tenta subir o
    arquivo ao nó remoto (comando ``se/file``), não suportado por todos
    os chromedrivers. O ``UselessFileDetector`` faz o ``send_keys`` passar
    o caminho literal ao <input>, correto para navegador + arquivo locais.
    """
    from selenium.webdriver.remote.file_detector import UselessFileDetector

    web_utils._navegador.file_detector = UselessFileDetector()


def resolver_caminho_navegador() -> Optional[str]:
    """Resolve o executável do Chrome: ENV, senão auto-detecção.

    Returns:
        Caminho do chrome.exe, ou None para deixar o py_rpautom decidir.
    """
    do_env = os.environ.get(ENV_NAVEGADOR_PATH)
    if do_env:
        return do_env

    for candidato in CAMINHOS_CHROME_CANDIDATOS:
        if candidato.is_file():
            return str(candidato)
    return None


def resolver_url_painel() -> str:
    """Resolve a URL do painel: ENV ALVO_PAINEL_URL ou produção.

    Permite apontar para uma cópia isolada (ex.: http://localhost:8000)
    durante testes, sem alterar o código.

    Returns:
        URL do painel a ser aberta.
    """
    return os.environ.get(ENV_URL, URL_PAINEL_PRODUCAO)


def painel_exige_login() -> bool:
    """Verifica se a tela de login está visível no painel.

    Returns:
        True quando o overlay de login está aberto (sessão inexistente).
    """
    script = (
        "var el = document.querySelector('%s');"
        'return !!(el && !el.hidden);' % SELETOR_LOGIN_SCREEN
    )
    return bool(web_utils.executar_script(script))


def autenticar_painel(credenciais: CredenciaisPainel) -> None:
    """Autentica no painel quando a tela de login está presente.

    Args:
        credenciais: Usuário e senha do perfil com acesso à importação.

    Raises:
        Exception: Propaga falhas de automação após log.
    """
    if not painel_exige_login():
        logger.info('Sessão já ativa; login não necessário.')
        return

    web_utils.aguardar_elemento(SELETOR_LOGIN_USUARIO)
    web_utils.escrever_em_elemento(SELETOR_LOGIN_USUARIO, credenciais.usuario)
    web_utils.escrever_em_elemento(SELETOR_LOGIN_SENHA, credenciais.senha)
    web_utils.clicar_elemento(SELETOR_LOGIN_SUBMIT)
    web_utils.aguardar_elemento(SELETOR_NAV_IMPORTAR)
    logger.info('Autenticado no painel como "%s".', credenciais.usuario)


def navegar_para_importacao() -> None:
    """Navega até a aba 'Importar Dados' do painel."""
    web_utils.aguardar_elemento(SELETOR_NAV_IMPORTAR)
    web_utils.clicar_elemento(SELETOR_NAV_IMPORTAR)
    logger.info('Aba "Importar Dados" acessada.')


def _revelar_input(seletor_input: str) -> None:
    """Torna interagível um <input type=file> oculto (atributo hidden)."""
    script = (
        "var el = document.querySelector('%s');"
        'if (el) {'
        " el.removeAttribute('hidden');"
        " el.style.display = 'block';"
        " el.style.visibility = 'visible';"
        " el.style.opacity = '1';"
        " el.style.height = '1px';"
        '}' % seletor_input
    )
    web_utils.executar_script(script)


def instalar_monitor_sincronizacao() -> None:
    """Instala um MutationObserver no container de toasts do painel.

    O painel grava o arquivo no localStorage na hora e dispara a
    sincronização com o Supabase de forma assíncrona (fire-and-forget),
    confirmando o resultado por um toast ("...sincronizado com servidor ✓"
    em sucesso; "Erro ao sincronizar..." em falha). Sem observar esse
    toast, a automação encerra o navegador antes de o Supabase responder e
    o arquivo nunca é sincronizado com o banco. O observer grava o último
    resultado por tipo em ``window.__rpaSync`` para consulta pela automação.
    """
    script = (
        'if (!window.__rpaSyncInstalado) {'
        '  window.__rpaSync = { extrato: null, retorno: null };'
        "  var alvo = document.querySelector('%s');"
        '  if (alvo) {'
        '    var registrar = function (node) {'
        '      if (!node || node.nodeType !== 1 || !node.classList) return;'
        "      var texto = (node.textContent || '').toLowerCase();"
        "      var chave = texto.indexOf('extrato') !== -1 ? 'extrato'"
        "        : (texto.indexOf('retorno') !== -1 ? 'retorno' : null);"
        '      if (!chave) return;'
        "      if (node.classList.contains('toast-success')"
        "          && texto.indexOf('sincronizado') !== -1) {"
        '        window.__rpaSync[chave] ='
        '          { ok: true, msg: node.textContent.trim() };'
        "      } else if (node.classList.contains('toast-error')) {"
        '        window.__rpaSync[chave] ='
        '          { ok: false, msg: node.textContent.trim() };'
        '      }'
        '    };'
        '    var obs = new MutationObserver(function (muts) {'
        '      muts.forEach(function (m) {'
        '        Array.prototype.forEach.call(m.addedNodes, registrar);'
        '      });'
        '    });'
        '    obs.observe(alvo, { childList: true });'
        '    window.__rpaSyncInstalado = true;'
        '  }'
        '}' % SELETOR_TOAST_CONTAINER
    )
    web_utils.executar_script(script)


def _reiniciar_sinal_sincronizacao(chave: str) -> None:
    """Zera o slot de sincronização antes de enviar um novo arquivo."""
    script = "if (window.__rpaSync) { window.__rpaSync['%s'] = null; }" % chave
    web_utils.executar_script(script)


def _consultar_sinal_sincronizacao(chave: str) -> Optional[dict]:
    """Lê o resultado de sincronização registrado para o tipo informado.

    Usa o driver Selenium diretamente porque ``web_utils.executar_script``
    descarta o valor de retorno do JS (retorna sempre True/False); aqui o
    valor de ``window.__rpaSync`` precisa ser propagado ao Python.

    Args:
        chave: 'extrato' ou 'retorno'.

    Returns:
        Dict ``{'ok': bool, 'msg': str}`` quando houve confirmação, senão
        None (sincronização ainda em andamento).
    """
    script = (
        "return (window.__rpaSync && window.__rpaSync['%s'])"
        " ? window.__rpaSync['%s'] : null;" % (chave, chave)
    )
    return web_utils._navegador.execute_script(script)


def _aguardar_sincronizacao(chave: str, rotulo: str) -> None:
    """Aguarda a confirmação de sincronização do arquivo com o Supabase.

    Args:
        chave: Slot monitorado em ``window.__rpaSync`` ('extrato'/'retorno').
        rotulo: Rótulo do insumo, para mensagens de erro e log.

    Raises:
        RuntimeError: Se o painel reportar erro de sincronização.
        TimeoutError: Se a confirmação não chegar dentro do tempo limite.
    """
    limite = time.monotonic() + TIMEOUT_SINCRONIZACAO_SEG
    while time.monotonic() < limite:
        sinal = _consultar_sinal_sincronizacao(chave)
        if sinal is not None:
            if sinal.get('ok'):
                logger.info(
                    'Sincronização confirmada para %s: %s',
                    rotulo,
                    sinal.get('msg'),
                )
                return
            raise RuntimeError(
                f'Falha ao sincronizar {rotulo}: {sinal.get("msg")}'
            )
        time.sleep(INTERVALO_POLL_SEG)
    raise TimeoutError(
        f'Sincronização de {rotulo} não confirmada em '
        f'{TIMEOUT_SINCRONIZACAO_SEG}s.'
    )


def importar_arquivo(
    seletor_input: str,
    caminho: Path,
    rotulo: str,
    chave_sync: str,
) -> None:
    """Envia um arquivo e aguarda a sincronização com o banco (Supabase).

    Diferente da confirmação visual do dropzone (que muda antes do sync
    concluir), aqui a espera é pelo toast de sincronização, garantindo que
    o dado chegou ao servidor antes de o navegador ser encerrado.

    Args:
        seletor_input: Seletor CSS do <input type=file>.
        caminho: Caminho absoluto do arquivo a enviar.
        rotulo: Rótulo do insumo, apenas para log.
        chave_sync: Slot em ``window.__rpaSync`` associado ao insumo.

    Raises:
        RuntimeError: Se a sincronização falhar no painel.
        TimeoutError: Se a sincronização não confirmar no tempo limite.
    """
    logger.info('Enviando %s: %s', rotulo, caminho)
    _revelar_input(seletor_input)
    _reiniciar_sinal_sincronizacao(chave_sync)
    # escrever_em_elemento (performar=False) faz apenas send_keys, sem
    # click/clear — forma correta de upload em <input type=file>.
    web_utils.escrever_em_elemento(seletor_input, str(caminho))
    _aguardar_sincronizacao(chave_sync, rotulo)
    logger.info('Importação e sincronização confirmadas para %s.', rotulo)


def importar_no_painel(
    arquivos: ArquivosImportacao,
    credenciais: CredenciaisPainel,
    navegador: str = 'chrome',
) -> None:
    """Orquestra a importação web dos dois insumos no painel Extrato_WEB.

    Args:
        arquivos: Caminhos validados de extrato e retorno.
        credenciais: Credenciais do perfil com acesso à importação.
        navegador: Navegador usado pelo py_rpautom.

    Raises:
        Exception: Propaga falhas de automação após log.
    """
    url = resolver_url_painel()
    caminho_navegador = resolver_caminho_navegador()
    logger.info('Abrindo painel: %s', url)
    if caminho_navegador:
        web_utils.iniciar_navegador(
            url, navegador, caminho_navegador=caminho_navegador
        )
    else:
        web_utils.iniciar_navegador(url, navegador)
    try:
        _configurar_upload_local()
        web_utils.esperar_pagina_carregar()
        autenticar_painel(credenciais)
        navegar_para_importacao()
        instalar_monitor_sincronizacao()

        logger.info('Início da importação automática no painel.')
        importar_arquivo(
            SELETOR_INPUT_EXTRATO,
            arquivos.extrato_bancario,
            ROTULO_EXTRATO,
            CHAVE_SYNC_EXTRATO,
        )
        importar_arquivo(
            SELETOR_INPUT_RETORNO,
            arquivos.retorno_bpo,
            ROTULO_RETORNO,
            CHAVE_SYNC_RETORNO,
        )
        logger.info('Término da importação automática no painel.')
    finally:
        web_utils.encerrar_navegador()


# --------------------------------------------------------------------------- #
# I/O — logging da validação
# --------------------------------------------------------------------------- #
def registrar_resultado_validacao(resultado: ResultadoValidacao) -> None:
    """Registra em log os insumos encontrados e ausentes.

    Args:
        resultado: Resultado da validação dos insumos obrigatórios.
    """
    for item in resultado.encontrados:
        logger.info('Arquivo encontrado — %s: %s', item.rotulo, item.caminho)

    for item in resultado.ausentes:
        logger.error('Arquivo AUSENTE — %s', item.rotulo)


# --------------------------------------------------------------------------- #
# I/O — notificação no Teams (etapa anterior à importação)
# --------------------------------------------------------------------------- #
def notificar_arquivos_no_teams(
    arquivos: ArquivosImportacao,
    data_referencia: date,
) -> bool:
    """Publica no chat do Teams os arquivos que serão anexados no painel.

    Roda antes da importação, para que o time consuma o dado direto do
    chat. É idempotente por data (checkpoint próprio) e best-effort: em
    caso de falha, apenas registra log — a importação segue.

    Args:
        arquivos: Insumos validados que serão anexados no painel.
        data_referencia: Data de referência da execução.

    Returns:
        True se os arquivos foram publicados agora ou já haviam sido
        publicados hoje; False se a etapa falhou ou está desativada.
    """
    if envio_teams_ja_concluido(data_referencia):
        logger.info(
            'Arquivos de %s já publicados no Teams hoje; envio ignorado.',
            data_referencia.strftime('%d/%m/%Y'),
        )
        return True

    caminhos = (arquivos.extrato_bancario, arquivos.retorno_bpo)
    try:
        enviado = enviar_arquivos_no_chat(caminhos, data_referencia)
    except ErroEnvioTeams as exc:
        logger.error(
            'Falha ao publicar os arquivos no Teams: %s. '
            'A importação no painel prossegue.',
            exc,
        )
        return False

    if enviado:
        registrar_envio_teams_concluido(data_referencia)
        logger.info('Arquivos publicados no chat do Teams.')
    return enviado


# --------------------------------------------------------------------------- #
# Orquestração
# --------------------------------------------------------------------------- #
def executar_importacao_automatica(
    data_referencia: date,
    navegador: str = 'chrome',
) -> bool:
    """Executa o fluxo: valida -> gate -> Teams -> importa no painel.

    Não move arquivos: os insumos permanecem nas pastas de origem.

    Args:
        data_referencia: Data usada no gate do e-mail Arbi do dia.
        navegador: Navegador usado pelo py_rpautom.

    Returns:
        True se a importação está garantida (executada agora ou já
        concluída antes no mesmo dia); False se abortada no gate.
    """
    if importacao_ja_concluida(data_referencia):
        logger.info(
            'Importação de %s já concluída anteriormente hoje; interrompida '
            'sem reimportar (evita descasamento na conciliação).',
            data_referencia.strftime('%d/%m/%Y'),
        )
        return True

    itens = montar_itens_obrigatorios(data_referencia)
    resultado = validar_arquivos_obrigatorios(itens)
    registrar_resultado_validacao(resultado)

    if not resultado.disponivel:
        faltantes = ', '.join(item.rotulo for item in resultado.ausentes)
        logger.critical(
            'Importação ABORTADA — falta de arquivos obrigatórios: %s. '
            'Verifique se o e-mail do Banco Arbi chegou hoje.',
            faltantes,
        )
        return False

    credenciais = carregar_credenciais()
    arquivos = montar_arquivos_importacao(resultado)
    notificar_arquivos_no_teams(arquivos, data_referencia)
    importar_no_painel(arquivos, credenciais, navegador=navegador)
    registrar_importacao_concluida(data_referencia)
    logger.info('Importação automática concluída com sucesso.')
    return True


def parsear_data_referencia(argumentos: Sequence[str]) -> date:
    """Resolve a data de referência a partir dos argumentos de linha.

    Args:
        argumentos: ``argv[1:]``; primeiro item opcional em DD-MM-AAAA.

    Returns:
        Data informada ou a data de hoje quando nenhum argumento é passado.

    Raises:
        ValueError: Se a data informada não estiver no formato DD-MM-AAAA.

    Example:
        >>> parsear_data_referencia(('05-08-2026',)).isoformat()
        '2026-08-05'
    """
    if not argumentos:
        return date.today()
    return datetime.strptime(argumentos[0], '%d-%m-%Y').date()


def configurar_logging() -> None:
    """Configura logging para arquivo e console."""
    PASTA_RAIZ.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] %(message)s',
        datefmt='%d/%m/%Y %H:%M:%S',
        handlers=[
            logging.FileHandler(CAMINHO_LOG, encoding='utf-8'),
            logging.StreamHandler(sys.stdout),
        ],
    )


def main() -> int:
    """Ponto de entrada de linha de comando.

    Returns:
        Código de saída: 0 em sucesso, 1 se abortado no gate.
    """
    configurar_logging()
    logger.info('=== Importação automática Extrato_WEB ===')
    try:
        data_referencia = parsear_data_referencia(tuple(sys.argv[1:]))
        logger.info(
            'Data de referência do gate: %s.',
            data_referencia.strftime('%d/%m/%Y'),
        )
        executou = executar_importacao_automatica(data_referencia)
    except Exception as exc:
        logger.exception('Erro durante a importação automática: %s', exc)
        return 2
    return 0 if executou else 1


if __name__ == '__main__':
    sys.exit(main())
