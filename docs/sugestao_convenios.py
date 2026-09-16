# INSERIR EM: automação_email_antigo/sugestao_convenios.py
# DEPENDÊNCIA: pip install pandas openpyxl
#
# Catalogação automática dos CNPJs que caem em "Validar com a Conciliação".
#
# Camadas de resolução (a seguinte só recebe o que a anterior não resolveu):
#   1. Match exato do documento — já feito por extrair_extratos.
#   2. Raiz do CNPJ (8 primeiros dígitos): filiais e órgãos do mesmo ente
#      compartilham a raiz. Alta confiança -> aplicada automaticamente.
#   3. Semelhança do NOME_CTP com o catálogo. Sempre vai para revisão
#      humana: similaridade textual confunde sequência ("CONSIGNADO II" x
#      "CONSIGNADO III"), o que criaria classificação errada permanente.
#
# A raiz só é usada quando aponta para um único convênio no catálogo —
# raiz ambígua não gera sugestão.

# --- stdlib ---
import difflib
import json
import logging
import re
import shutil
import sys
import time
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Iterable, Mapping, Optional, Sequence

# --- terceiros ---
import pandas as pd
import requests

# --------------------------------------------------------------------------- #
# Constantes
# --------------------------------------------------------------------------- #
ARQUIVO_CATALOGO = Path(r"C:\RPA\automação_email_antigo\CNPJ'S.xlsx")
PASTA_RAIZ = Path(r'C:\RPA\automação_email_antigo\extratos')
PASTA_BACKUP = PASTA_RAIZ / 'catalogo_backup'
CAMINHO_AUDITORIA = PASTA_RAIZ / 'auditoria_convenios.csv'

GUIA_CATALOGO = 'BD_CONVENIOS'
GUIA_REVISAO = 'SUGESTOES_PENDENTES'
COLUNA_DOCUMENTO = 'DocumentoFederal'
COLUNA_CONVENIO = 'Convênios'
STATUS_PENDENTE = 'PENDENTE'

CONVENIO_PENDENTE = 'Validar com a Conciliação'
COLUNA_DOCUMENTO_EXTRATO = 'CGC_CPF_CTP'
COLUNA_NOME_EXTRATO = 'NOME_CTP'

TAMANHO_CNPJ = 14
TAMANHO_RAIZ = 8
TAMANHO_MINIMO_CNPJ = 12
RAIZ_INVALIDA = '00000000'

# Só a raiz do CNPJ entra sozinha na base. A semelhança de nome fica abaixo
# do corte de propósito — ela sugere, quem decide é a Conciliação.
CONFIANCA_MINIMA_AUTOMATICA = 0.90
CONFIANCA_RAIZ = 0.95
CONFIANCA_MAXIMA_NOME = 0.80
CORTE_SIMILARIDADE_NOME = 0.72

# "MUNICIPIO DE TERESINA" e "Pref. Teresina" são o mesmo ente escrito de
# formas diferentes; a equivalência aproxima o nome do padrão do catálogo.
PREFIXOS_EQUIVALENTES = (
    ('prefeitura municipal de ', 'pref. '),
    ('municipio de ', 'pref. '),
    ('governo do estado do ', 'gov. '),
    ('governo do estado da ', 'gov. '),
    ('governo do estado de ', 'gov. '),
    ('estado do ', 'gov. '),
)

# --------------------------------------------------------------------------- #
# Consulta externa de CNPJ (camada 3)
# --------------------------------------------------------------------------- #
# Provedores públicos e gratuitos, tentados em cascata. A BrasilAPI aplica
# rate limit por IP (HTTP 429), então a MinhaReceita vem primeiro. Consulta
# externa é enriquecimento: indisponibilidade nunca interrompe o processo.
PROVEDORES_CNPJ = (
    'https://minhareceita.org/{documento}',
    'https://brasilapi.com.br/api/cnpj/v1/{documento}',
)
CAMINHO_CACHE_CNPJ = PASTA_RAIZ / 'cache_cnpj.json'
TIMEOUT_CONSULTA_SEG = 15
INTERVALO_ENTRE_CONSULTAS_SEG = 0.6

# A natureza jurídica distingue o ente: um fundo municipal pertence à
# prefeitura do seu município, mas uma empresa privada sediada no mesmo
# município não pertence a ninguém. Sem esse filtro, a geografia
# classificaria qualquer CNPJ pela cidade da sede.
TERMO_NATUREZA_MUNICIPAL = 'municipal'
TERMO_NATUREZA_ESTADUAL = 'estadual'
ESFERA_MUNICIPAL = 'municipal'
ESFERA_ESTADUAL = 'estadual'
PREFIXO_PREFEITURA = 'pref. '
PREFIXO_GOVERNO = 'gov. '
CONFIANCA_CONSULTA_EXTERNA = 0.92

UF_PARA_ESTADO = {
    'AC': 'Acre',
    'AL': 'Alagoas',
    'AM': 'Amazonas',
    'AP': 'Amapá',
    'BA': 'Bahia',
    'CE': 'Ceará',
    'DF': 'Distrito Federal',
    'ES': 'Espirito Santo',
    'GO': 'Goiás',
    'MA': 'Maranhão',
    'MG': 'Minas Gerais',
    'MS': 'Mato Grosso do Sul',
    'MT': 'Mato Grosso',
    'PA': 'Pará',
    'PB': 'Paraíba',
    'PE': 'Pernambuco',
    'PI': 'Piauí',
    'PR': 'Paraná',
    'RJ': 'Rio de Janeiro',
    'RN': 'Rio Grande do Norte',
    'RO': 'Rondônia',
    'RR': 'Roraima',
    'RS': 'Rio Grande do Sul',
    'SC': 'Santa Catarina',
    'SE': 'Sergipe',
    'SP': 'São Paulo',
    'TO': 'Tocantins',
}

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Dataclasses (imutáveis)
# --------------------------------------------------------------------------- #
class OrigemSugestao(str, Enum):
    """De onde veio a sugestão de convênio."""

    RAIZ_CNPJ = 'raiz_cnpj'
    SEMELHANCA_NOME = 'semelhanca_nome'
    CONSULTA_EXTERNA = 'consulta_externa'


@dataclass(frozen=True)
class Pendencia:
    """Documento não catalogado, com o nome que veio no extrato."""

    documento: str
    nome_extrato: str
    ocorrencias: int


@dataclass(frozen=True)
class Sugestao:
    """Convênio proposto para um documento pendente."""

    documento: str
    nome_extrato: str
    convenio: str
    origem: OrigemSugestao
    confianca: float
    ocorrencias: int
    detalhe: str = ''


@dataclass(frozen=True)
class DadosCnpj:
    """Dados públicos retornados pela consulta externa."""

    documento: str
    razao_social: str
    municipio: str
    uf: str
    natureza_juridica: str


@dataclass(frozen=True)
class ResultadoCatalogacao:
    """Saída de uma rodada de catalogação."""

    pendencias: tuple
    aplicadas: tuple
    para_revisao: tuple
    sem_pista: tuple


@dataclass(frozen=True)
class Catalogo:
    """Catálogo indexado por documento e por raiz de CNPJ (só unânimes)."""

    por_documento: Mapping[str, str]
    por_raiz: Mapping[str, str]
    convenios: tuple


# --------------------------------------------------------------------------- #
# Funções puras — normalização
# --------------------------------------------------------------------------- #
def normalizar_documento(valor) -> str:
    """Reduz o documento a dígitos e completa zeros à esquerda de CNPJ.

    O extrato traz o CNPJ sem os zeros iniciais ('4312369000190') enquanto
    o catálogo pode trazê-lo completo — sem normalizar, o match exato falha.
    CPF (11 dígitos) é preservado como está.

    Args:
        valor: Documento em qualquer formato (com ou sem máscara).

    Returns:
        String só de dígitos, com 14 posições quando for CNPJ.

    Example:
        >>> normalizar_documento('04.312.369/0001-90')
        '04312369000190'
    """
    digitos = re.sub(r'\D', '', str(valor or ''))
    if TAMANHO_MINIMO_CNPJ <= len(digitos) <= TAMANHO_CNPJ:
        return digitos.zfill(TAMANHO_CNPJ)
    return digitos


def raiz_documento(documento: str) -> str:
    """Extrai a raiz (8 primeiros dígitos) de um CNPJ normalizado.

    Args:
        documento: Documento já normalizado.

    Returns:
        A raiz, ou string vazia se o documento não for CNPJ ou a raiz for
        zerada (artefato de documento malformado no catálogo).
    """
    normalizado = normalizar_documento(documento)
    if len(normalizado) != TAMANHO_CNPJ:
        return ''

    raiz = normalizado[:TAMANHO_RAIZ]
    return '' if raiz == RAIZ_INVALIDA else raiz


def normalizar_nome(texto) -> str:
    """Remove acentos, espaços redundantes e caixa para comparação."""
    sem_acento = unicodedata.normalize('NFD', str(texto or ''))
    limpo = ''.join(c for c in sem_acento if unicodedata.category(c) != 'Mn')
    return re.sub(r'\s+', ' ', limpo).strip().lower()


def aproximar_do_padrao(nome) -> str:
    """Reescreve o nome do extrato no padrão de nomenclatura do catálogo."""
    normalizado = normalizar_nome(nome)
    for origem, destino in PREFIXOS_EQUIVALENTES:
        if normalizado.startswith(origem):
            return destino + normalizado[len(origem) :]
    return normalizado


# --------------------------------------------------------------------------- #
# Funções puras — catálogo e sugestão
# --------------------------------------------------------------------------- #
def montar_catalogo(pares: Iterable) -> Catalogo:
    """Indexa o catálogo por documento e por raiz de CNPJ.

    A raiz só é indexada quando todos os documentos dela apontam para o
    mesmo convênio — raiz ambígua é descartada para não gerar sugestão
    errada.

    Args:
        pares: Iterável de (documento, convênio).

    Returns:
        Catálogo pronto para consulta.
    """
    por_documento = {}
    convenios_por_raiz = {}

    for documento, convenio in pares:
        normalizado = normalizar_documento(documento)
        if not normalizado or not str(convenio or '').strip():
            continue

        por_documento[normalizado] = convenio
        raiz = raiz_documento(normalizado)
        if raiz:
            convenios_por_raiz.setdefault(raiz, set()).add(convenio)

    por_raiz = {
        raiz: next(iter(convenios))
        for raiz, convenios in convenios_por_raiz.items()
        if len(convenios) == 1
    }

    return Catalogo(
        por_documento=por_documento,
        por_raiz=por_raiz,
        convenios=tuple(sorted(set(por_documento.values()))),
    )


def _sugerir_por_nome(
    pendencia: Pendencia, catalogo: Catalogo
) -> Optional[Sugestao]:
    """Procura no catálogo o convênio de nome mais próximo ao do extrato."""
    indice = {normalizar_nome(c): c for c in catalogo.convenios}
    candidato = aproximar_do_padrao(pendencia.nome_extrato)

    proximos = difflib.get_close_matches(
        candidato, list(indice), n=1, cutoff=CORTE_SIMILARIDADE_NOME
    )
    if not proximos:
        return None

    similaridade = difflib.SequenceMatcher(
        None, candidato, proximos[0]
    ).ratio()

    return Sugestao(
        documento=pendencia.documento,
        nome_extrato=pendencia.nome_extrato,
        convenio=indice[proximos[0]],
        origem=OrigemSugestao.SEMELHANCA_NOME,
        confianca=min(similaridade, CONFIANCA_MAXIMA_NOME),
        ocorrencias=pendencia.ocorrencias,
    )


def sugerir(pendencia: Pendencia, catalogo: Catalogo) -> Optional[Sugestao]:
    """Propõe um convênio para o documento pendente.

    Args:
        pendencia: Documento não catalogado e seu nome no extrato.
        catalogo: Catálogo indexado.

    Returns:
        A sugestão, ou None se o documento já está catalogado ou não há
        pista suficiente.

    Example:
        >>> catalogo = montar_catalogo((('04312419000190', 'Gov. Amazonas'),))
        >>> sugerir(Pendencia('04312419000130', 'SEDUC', 5), catalogo).convenio
        'Gov. Amazonas'
    """
    documento = normalizar_documento(pendencia.documento)
    if documento in catalogo.por_documento:
        return None

    convenio_raiz = catalogo.por_raiz.get(raiz_documento(documento))
    if convenio_raiz:
        return Sugestao(
            documento=documento,
            nome_extrato=pendencia.nome_extrato,
            convenio=convenio_raiz,
            origem=OrigemSugestao.RAIZ_CNPJ,
            confianca=CONFIANCA_RAIZ,
            ocorrencias=pendencia.ocorrencias,
        )

    return _sugerir_por_nome(pendencia, catalogo)


def _esfera_do_ente(dados: DadosCnpj) -> str:
    """Classifica a natureza jurídica em 'municipal', 'estadual' ou ''.

    String vazia significa que não é administração pública — e portanto a
    geografia não diz nada sobre o convênio.
    """
    natureza = normalizar_nome(dados.natureza_juridica)

    if TERMO_NATUREZA_MUNICIPAL in natureza or natureza.startswith(
        'municipio'
    ):
        return ESFERA_MUNICIPAL

    if TERMO_NATUREZA_ESTADUAL in natureza or natureza.startswith('estado'):
        return ESFERA_ESTADUAL

    return ''


def _e_ente_publico(dados: DadosCnpj) -> bool:
    """Indica se a natureza jurídica é de administração pública."""
    return bool(_esfera_do_ente(dados))


def _convenio_do_ente(dados: DadosCnpj, catalogo: Catalogo) -> Optional[str]:
    """Casa o ente público com o convênio correspondente do catálogo.

    Ente municipal casa com 'Pref. <município>'; ente estadual, com
    'Gov. <estado>'. Qualquer outra natureza jurídica não casa por
    geografia — a sede de uma empresa privada não a torna do município.
    """
    esfera = _esfera_do_ente(dados)
    indice = {normalizar_nome(c): c for c in catalogo.convenios}

    if esfera == ESFERA_MUNICIPAL:
        chave = normalizar_nome(PREFIXO_PREFEITURA + dados.municipio)
        return indice.get(chave)

    if esfera == ESFERA_ESTADUAL:
        estado = UF_PARA_ESTADO.get(str(dados.uf or '').upper())
        if not estado:
            return None
        return indice.get(normalizar_nome(PREFIXO_GOVERNO + estado))

    return None


def sugerir_por_dados_externos(
    pendencia: Pendencia,
    dados: Optional[DadosCnpj],
    catalogo: Catalogo,
) -> Optional[Sugestao]:
    """Propõe convênio a partir dos dados públicos do CNPJ.

    Args:
        pendencia: Documento sem pista nas camadas anteriores.
        dados: Retorno da consulta externa (None quando indisponível).
        catalogo: Catálogo indexado.

    Returns:
        Sugestão de alta confiança quando o ente público casa com um
        convênio do catálogo; caso contrário, tenta semelhança pela razão
        social (baixa confiança) ou None.
    """
    if dados is None:
        return None

    detalhe = f'{dados.razao_social} — {dados.municipio}/{dados.uf}'
    convenio = _convenio_do_ente(dados, catalogo)

    if convenio:
        return Sugestao(
            documento=pendencia.documento,
            nome_extrato=pendencia.nome_extrato,
            convenio=convenio,
            origem=OrigemSugestao.CONSULTA_EXTERNA,
            confianca=CONFIANCA_CONSULTA_EXTERNA,
            ocorrencias=pendencia.ocorrencias,
            detalhe=detalhe,
        )

    # Ente público identificado sem convênio correspondente: a resposta
    # certa é "não temos convênio com ele", não um palpite textual. Sem
    # este corte, 'Município de Três Rios' vira 'Pref. Teresina' por
    # semelhança de string.
    if _e_ente_publico(dados):
        return None

    por_razao_social = _sugerir_por_nome(
        Pendencia(
            documento=pendencia.documento,
            nome_extrato=dados.razao_social,
            ocorrencias=pendencia.ocorrencias,
        ),
        catalogo,
    )
    if not por_razao_social:
        return None

    return Sugestao(
        documento=por_razao_social.documento,
        nome_extrato=pendencia.nome_extrato,
        convenio=por_razao_social.convenio,
        origem=OrigemSugestao.CONSULTA_EXTERNA,
        confianca=por_razao_social.confianca,
        ocorrencias=pendencia.ocorrencias,
        detalhe=detalhe,
    )


def consolidar_pendencias(linhas: Iterable) -> tuple:
    """Agrupa lançamentos pendentes por documento, contando ocorrências.

    Args:
        linhas: Iterável de (documento, nome_no_extrato).

    Returns:
        Tupla de Pendencia ordenada por ocorrências (desc).
    """
    agrupado = {}

    for documento, nome in linhas:
        normalizado = normalizar_documento(documento)
        if not normalizado:
            continue

        atual = agrupado.get(normalizado)
        agrupado[normalizado] = Pendencia(
            documento=normalizado,
            nome_extrato=atual.nome_extrato if atual else str(nome or ''),
            ocorrencias=(atual.ocorrencias if atual else 0) + 1,
        )

    return tuple(sorted(agrupado.values(), key=lambda p: -p.ocorrencias))


def separar_por_confianca(sugestoes: Iterable) -> tuple:
    """Divide as sugestões entre aplicáveis automaticamente e para revisão.

    Args:
        sugestoes: Sugestões geradas.

    Returns:
        Tupla (aplicaveis, para_revisao).
    """
    itens = tuple(sugestoes)
    aplicaveis = tuple(
        s for s in itens if s.confianca >= CONFIANCA_MINIMA_AUTOMATICA
    )
    para_revisao = tuple(
        s for s in itens if s.confianca < CONFIANCA_MINIMA_AUTOMATICA
    )
    return aplicaveis, para_revisao


# --------------------------------------------------------------------------- #
# I/O — leitura
# --------------------------------------------------------------------------- #
def carregar_catalogo(caminho: Path = ARQUIVO_CATALOGO) -> Catalogo:
    """Lê o catálogo de convênios do Excel.

    Args:
        caminho: Caminho do arquivo de convênios.

    Returns:
        Catálogo indexado.
    """
    df = pd.read_excel(caminho, sheet_name=GUIA_CATALOGO, dtype=str)
    return montar_catalogo(zip(df[COLUNA_DOCUMENTO], df[COLUNA_CONVENIO]))


def coletar_pendencias(caminhos: Sequence) -> tuple:
    """Lê os consolidados e extrai os documentos ainda não classificados.

    Args:
        caminhos: Caminhos dos arquivos consolidados (.xlsx).

    Returns:
        Tupla de Pendencia agregada de todos os arquivos.
    """
    linhas = []

    for caminho in caminhos:
        if not Path(caminho).is_file():
            continue

        df = pd.read_excel(caminho, dtype=str)
        if COLUNA_CONVENIO not in df.columns:
            continue

        pendentes = df[
            df[COLUNA_CONVENIO].astype(str).str.strip() == CONVENIO_PENDENTE
        ]
        linhas.extend(
            zip(
                pendentes[COLUNA_DOCUMENTO_EXTRATO],
                pendentes[COLUNA_NOME_EXTRATO],
            )
        )

    return consolidar_pendencias(linhas)


# --------------------------------------------------------------------------- #
# I/O — escrita
# --------------------------------------------------------------------------- #
def _fazer_backup(caminho: Path) -> Path:
    """Copia o catálogo para a pasta de backup com carimbo de tempo."""
    PASTA_BACKUP.mkdir(parents=True, exist_ok=True)
    momento = datetime.now().strftime('%Y%m%d_%H%M%S')
    destino = PASTA_BACKUP / f'{caminho.stem}_{momento}{caminho.suffix}'
    shutil.copy2(caminho, destino)
    logger.info('Backup do catálogo criado: %s', destino)
    return destino


def aplicar_no_catalogo(caminho: Path, sugestoes: Iterable) -> Path:
    """Acrescenta as sugestões ao catálogo, preservando o que já existe.

    Idempotente: documento já catalogado é ignorado. Sempre faz backup
    antes de gravar.

    Args:
        caminho: Caminho do arquivo de convênios.
        sugestoes: Sugestões aprovadas para aplicação.

    Returns:
        Caminho do backup criado.
    """
    caminho = Path(caminho)
    backup = _fazer_backup(caminho)

    df = pd.read_excel(caminho, sheet_name=GUIA_CATALOGO, dtype=str)
    ja_catalogados = {normalizar_documento(d) for d in df[COLUNA_DOCUMENTO]}

    novas = [
        {
            COLUNA_DOCUMENTO: s.documento,
            COLUNA_CONVENIO: s.convenio,
        }
        for s in sugestoes
        if normalizar_documento(s.documento) not in ja_catalogados
    ]

    if not novas:
        logger.info('Nenhum documento novo para acrescentar ao catálogo.')
        return backup

    df_final = pd.concat([df, pd.DataFrame(novas)], ignore_index=True)
    with pd.ExcelWriter(
        caminho, engine='openpyxl', mode='a', if_sheet_exists='replace'
    ) as writer:
        df_final.to_excel(writer, sheet_name=GUIA_CATALOGO, index=False)

    logger.info(
        '%s documento(s) acrescentado(s) ao catálogo (total: %s).',
        len(novas),
        len(df_final),
    )
    return backup


def _linha_revisao(**campos) -> dict:
    """Monta uma linha da guia de revisão com as colunas na ordem fixa."""
    return {
        COLUNA_DOCUMENTO: campos['documento'],
        'NomeNoExtrato': campos['nome_extrato'],
        'ConvenioSugerido': campos['convenio'],
        'Origem': campos['origem'],
        'Confianca': campos['confianca'],
        'Ocorrencias': campos['ocorrencias'],
        'DadosPublicos': campos['detalhe'],
        'Status': STATUS_PENDENTE,
        'GeradoEm': datetime.now().strftime('%d/%m/%Y %H:%M:%S'),
    }


def gravar_sugestoes_para_revisao(
    caminho: Path,
    sugestoes: Iterable,
    sem_pista: Iterable = (),
    detalhes: Optional[Mapping] = None,
) -> int:
    """Grava na guia de revisão o que exige decisão humana.

    Inclui tanto as sugestões de baixa confiança quanto os documentos sem
    pista — estes com os dados públicos do CNPJ, que é o que a Conciliação
    precisa para cadastrar um convênio novo.

    Args:
        caminho: Caminho do arquivo de convênios.
        sugestoes: Sugestões que exigem decisão humana.
        sem_pista: Pendências que nenhuma camada resolveu.
        detalhes: Mapa documento -> dados públicos, quando consultados.

    Returns:
        Quantidade de linhas gravadas.
    """
    itens = tuple(sugestoes)
    orfaos = tuple(sem_pista)
    if not itens and not orfaos:
        return 0

    _fazer_backup(Path(caminho))
    mapa_detalhes = detalhes or {}

    linhas = [
        _linha_revisao(
            documento=s.documento,
            nome_extrato=s.nome_extrato,
            convenio=s.convenio,
            origem=s.origem.value,
            confianca=round(s.confianca, 3),
            ocorrencias=s.ocorrencias,
            detalhe=s.detalhe,
        )
        for s in itens
    ] + [
        _linha_revisao(
            documento=p.documento,
            nome_extrato=p.nome_extrato,
            convenio='',
            origem='sem_pista',
            confianca='',
            ocorrencias=p.ocorrencias,
            detalhe=mapa_detalhes.get(p.documento, ''),
        )
        for p in orfaos
    ]

    df = pd.DataFrame(linhas)

    with pd.ExcelWriter(
        Path(caminho), engine='openpyxl', mode='a', if_sheet_exists='replace'
    ) as writer:
        df.to_excel(writer, sheet_name=GUIA_REVISAO, index=False)

    logger.info(
        '%s linha(s) gravada(s) para revisão (%s sem pista).',
        len(linhas),
        len(orfaos),
    )
    return len(linhas)


def _ler_cache_cnpj() -> dict:
    """Lê o cache local de consultas (vazio se ausente ou corrompido)."""
    caminho = Path(CAMINHO_CACHE_CNPJ)
    if not caminho.is_file():
        return {}
    try:
        return json.loads(caminho.read_text(encoding='utf-8'))
    except (ValueError, OSError) as exc:
        logger.warning('Cache de CNPJ ilegível (%s); será recriado.', exc)
        return {}


def _gravar_cache_cnpj(cache: dict) -> None:
    """Persiste o cache local de consultas."""
    caminho = Path(CAMINHO_CACHE_CNPJ)
    caminho.parent.mkdir(parents=True, exist_ok=True)
    caminho.write_text(
        json.dumps(cache, ensure_ascii=False, indent=2), encoding='utf-8'
    )


def _baixar_dados_cnpj(documento: str, provedor: str) -> dict:
    """Baixa os dados de um provedor. Levanta exceção em qualquer falha."""
    resposta = requests.get(
        provedor.format(documento=documento), timeout=TIMEOUT_CONSULTA_SEG
    )
    resposta.raise_for_status()
    return resposta.json()


def consultar_documento(documento: str) -> Optional[DadosCnpj]:
    """Consulta os dados públicos do CNPJ, com cache e cascata de provedores.

    Nunca levanta exceção: indisponibilidade, rate limit ou documento
    inexistente resultam em None, e o processo segue sem a camada externa.

    Args:
        documento: CNPJ normalizado.

    Returns:
        Dados do CNPJ, ou None se nenhum provedor respondeu.
    """
    cache = _ler_cache_cnpj()
    bruto = cache.get(documento)

    if bruto is None:
        for provedor in PROVEDORES_CNPJ:
            try:
                bruto = _baixar_dados_cnpj(documento, provedor)
                cache[documento] = bruto
                _gravar_cache_cnpj(cache)
                break
            except Exception as exc:
                logger.warning(
                    'Consulta de %s falhou em %s: %s',
                    documento,
                    provedor.split('/')[2],
                    exc,
                )
                time.sleep(INTERVALO_ENTRE_CONSULTAS_SEG)

    if not bruto:
        return None

    return DadosCnpj(
        documento=documento,
        razao_social=str(bruto.get('razao_social') or ''),
        municipio=str(bruto.get('municipio') or ''),
        uf=str(bruto.get('uf') or ''),
        natureza_juridica=str(bruto.get('natureza_juridica') or ''),
    )


def enriquecer_sem_pista(
    pendencias: Sequence,
    catalogo: Catalogo,
    consultor=consultar_documento,
) -> tuple:
    """Consulta os documentos sem pista e devolve as sugestões obtidas.

    Falha em um documento não interrompe os demais — requisito explícito:
    API fora do ar não pode impedir a execução do processo.

    Args:
        pendencias: Documentos que as camadas anteriores não resolveram.
        catalogo: Catálogo indexado.
        consultor: Função de consulta (injetável para teste).

    Returns:
        Tupla de sugestões obtidas via consulta externa.
    """
    sugestoes = []

    for pendencia in pendencias:
        try:
            dados = consultor(pendencia.documento)
        except Exception as exc:
            logger.warning(
                'Consulta externa indisponível para %s: %s',
                pendencia.documento,
                exc,
            )
            continue

        sugestao = sugerir_por_dados_externos(pendencia, dados, catalogo)
        if sugestao:
            sugestoes.append(sugestao)

    return tuple(sugestoes)


def _descrever_documentos(pendencias: Sequence) -> dict:
    """Descreve documentos pelos dados públicos já consultados (cache).

    Args:
        pendencias: Documentos a descrever.

    Returns:
        Mapa documento -> 'RAZÃO SOCIAL — MUNICÍPIO/UF (natureza)'.
    """
    descricoes = {}

    for pendencia in pendencias:
        try:
            dados = consultar_documento(pendencia.documento)
        except Exception as exc:
            logger.warning(
                'Sem dados públicos para %s: %s', pendencia.documento, exc
            )
            continue

        if dados:
            descricoes[pendencia.documento] = (
                f'{dados.razao_social} — {dados.municipio}/{dados.uf} '
                f'({dados.natureza_juridica})'
            )

    return descricoes


def executar_catalogacao(
    caminhos_consolidados: Sequence,
    caminho_catalogo: Path = ARQUIVO_CATALOGO,
    simular: bool = False,
    consultar_externo: bool = True,
) -> ResultadoCatalogacao:
    """Roda a catalogação: coleta pendências, sugere e grava o resultado.

    Sugestão por raiz de CNPJ entra sozinha no catálogo; sugestão por
    semelhança de nome vai para a guia de revisão. Em modo simulação nada
    é gravado — serve para conferir antes de aplicar.

    Args:
        caminhos_consolidados: Consolidados a varrer.
        caminho_catalogo: Arquivo de convênios.
        simular: Se True, apenas calcula e devolve o que faria.

    Returns:
        Resultado com pendências, aplicadas, para revisão e sem pista.
    """
    catalogo = carregar_catalogo(caminho_catalogo)
    pendencias = coletar_pendencias(caminhos_consolidados)

    sugestoes = tuple(filter(None, (sugerir(p, catalogo) for p in pendencias)))
    documentos_sugeridos = {s.documento for s in sugestoes}
    sem_pista = tuple(
        p for p in pendencias if p.documento not in documentos_sugeridos
    )

    detalhes_externos = {}
    if consultar_externo and sem_pista:
        externas = enriquecer_sem_pista(sem_pista, catalogo)
        resolvidos = {s.documento for s in externas}
        sugestoes += externas
        sem_pista = tuple(
            p for p in sem_pista if p.documento not in resolvidos
        )
        detalhes_externos = _descrever_documentos(sem_pista)

    aplicaveis, para_revisao = separar_por_confianca(sugestoes)

    if not simular:
        if aplicaveis:
            aplicar_no_catalogo(caminho_catalogo, aplicaveis)
            registrar_auditoria(aplicaveis, aplicadas=True)
        if para_revisao or sem_pista:
            gravar_sugestoes_para_revisao(
                caminho_catalogo,
                para_revisao,
                sem_pista=sem_pista,
                detalhes=detalhes_externos,
            )
            registrar_auditoria(para_revisao, aplicadas=False)

    logger.info(
        'Catalogação: %s pendência(s), %s aplicada(s), %s p/ revisão, '
        '%s sem pista.%s',
        len(pendencias),
        len(aplicaveis),
        len(para_revisao),
        len(sem_pista),
        ' (simulação)' if simular else '',
    )

    return ResultadoCatalogacao(
        pendencias=pendencias,
        aplicadas=aplicaveis,
        para_revisao=para_revisao,
        sem_pista=sem_pista,
    )


def configurar_logging() -> None:
    """Configura logging em console para execução avulsa."""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] %(message)s',
        datefmt='%d/%m/%Y %H:%M:%S',
    )


def main() -> int:
    """Ponto de entrada avulso: `python sugestao_convenios.py [--simular]`.

    Returns:
        0 em sucesso.
    """
    configurar_logging()

    # Import tardio: extrair_extratos importa este módulo, e a
    # reclassificação depende da regra de classificação que vive lá.
    from extrair_extratos import catalogar_convenios_pendentes

    argumentos = sys.argv[1:]
    catalogar_convenios_pendentes(
        simular='--simular' in argumentos,
        consultar_externo='--sem-api' not in argumentos,
    )
    return 0


def registrar_auditoria(
    sugestoes: Iterable,
    aplicadas: bool,
    caminho: Optional[Path] = None,
) -> None:
    """Anexa ao CSV de auditoria o que foi sugerido e se entrou na base.

    Args:
        sugestoes: Sugestões processadas.
        aplicadas: True se foram aplicadas ao catálogo.
        caminho: CSV de auditoria; usa CAMINHO_AUDITORIA quando omitido.
            Resolvido em tempo de chamada — default fixo no parâmetro
            congelaria o valor no import e ignoraria configuração.
    """
    itens = tuple(sugestoes)
    if not itens:
        return

    df = pd.DataFrame(
        [
            {
                'momento': datetime.now().strftime('%d/%m/%Y %H:%M:%S'),
                'documento': s.documento,
                'nome_extrato': s.nome_extrato,
                'convenio': s.convenio,
                'origem': s.origem.value,
                'confianca': round(s.confianca, 3),
                'ocorrencias': s.ocorrencias,
                'dados_publicos': s.detalhe,
                'aplicada': 'S' if aplicadas else 'N',
            }
            for s in itens
        ]
    )

    caminho = Path(caminho or CAMINHO_AUDITORIA)
    caminho.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(
        caminho,
        sep=';',
        index=False,
        mode='a' if caminho.is_file() else 'w',
        header=not caminho.is_file(),
        encoding='utf-8-sig',
    )


if __name__ == '__main__':
    sys.exit(main())
