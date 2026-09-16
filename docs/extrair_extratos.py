import glob
import logging
import os
import re
import shutil
import sys
from datetime import date, datetime, timedelta

import pandas as pd
import win32com.client

from sugestao_convenios import executar_catalogacao, normalizar_documento

PADRAO_REMETENTE = re.compile(
    r'^EXTRATO\d*@BANCOARBI\.COM\.BR$', re.IGNORECASE
)
EMAIL_DESTINO = 'extrato@alvocard.com.br'
PASTA_RAIZ = r'C:\RPA\automação_email_antigo\extratos'
ARQUIVO_CONVENIOS = r"C:\RPA\automação_email_antigo\CNPJ'S.xlsx"
PASTA_ORIGEM_BPO = r'C:\Users\JoãodeMeloRodriguesD\ALVO CARD\ALVO CARD - OPERAÇÕES\33. Indicadores Operações\02. Banco de Dados\26. BPO\01. Consolidado arquivo retorno'
PASTA_DESTINO_BPO = os.path.join(PASTA_RAIZ, 'BPO')
PADRAO_NOME_BPO = re.compile(
    r'^(\d{8})_\d{6}_consolidacao_arquivo_retorno', re.IGNORECASE
)

CONTAS = {
    '570031521': 'ALVO CARD',
    '570031424': 'EI CARD',
    '570031459': 'VEM BENEFICIOS',
    '570031475': 'JUNTOS CARD',
}

TAMANHO_CPF = 11
PENDENTE_CONVENIO = 'Validar com a Conciliação'

# O CSV do Arbi traz DATA_MOVIMENTACAO como texto DD/MM/AAAA HH:MM:SS. Se ela
# for gravada assim no consolidado, o painel Extrato_WEB (JavaScript) resolve
# a string com `new Date(...)`, que interpreta DD/MM como MM/DD: '10/08/2026'
# vira outubro e '21/07/2026' vira data inválida (descartada da conciliação).
# Por isso a coluna é convertida para datetime real antes do to_excel.
COLUNA_DATA_MOVIMENTACAO = 'DATA_MOVIMENTACAO'
PADRAO_DATA_BR = re.compile(r'^\d{2}/\d{2}/\d{4}')
FORMATOS_DATA_BR = ('%d/%m/%Y %H:%M:%S', '%d/%m/%Y')

# O extrato do Arbi usa 31/12/1899 (com documento 0000000 e valor zerado)
# como marcador de dia sem movimentação. Não é data de lançamento: vira
# vazio para não sujar a série temporal da conciliação. O Excel também não
# representa datas anteriores ao seu epoch, então gravá-la seria inócuo.
DATA_SENTINELA_SEM_MOVIMENTO = pd.Timestamp(1899, 12, 31)


def caminho_consolidado(empresa: str) -> str:
    pasta_empresa = os.path.join(PASTA_RAIZ, empresa)
    os.makedirs(pasta_empresa, exist_ok=True)
    return os.path.join(pasta_empresa, f'consolidado_{empresa}.xlsx')


os.makedirs(PASTA_RAIZ, exist_ok=True)
log_path = os.path.join(PASTA_RAIZ, 'log.txt')

logging.basicConfig(
    filename=log_path,
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%d/%m/%Y %H:%M:%S',
)
logger = logging.getLogger(__name__)


def carregar_convenios() -> dict:
    try:
        df = pd.read_excel(
            ARQUIVO_CONVENIOS, sheet_name='BD_CONVENIOS', dtype=str
        )
        df['DocumentoFederal'] = df['DocumentoFederal'].apply(
            normalizar_documento
        )
        df = df.drop_duplicates(subset='DocumentoFederal')
        mapa = dict(zip(df['DocumentoFederal'], df['Convênios']))
        logger.info(
            f'Tabela de convenios carregada: {len(mapa)} CNPJ/CPF unicos.'
        )
        return mapa
    except Exception as e:
        logger.error(f'Erro ao carregar tabela de convenios: {e}')
        return {}


def classificar_convenio(
    cgc_cpf_ctp: str, historico_descricao: str, mapa_convenios: dict
) -> str:
    doc_raw = str(cgc_cpf_ctp).strip() if pd.notna(cgc_cpf_ctp) else ''
    hist = (
        str(historico_descricao).upper()
        if pd.notna(historico_descricao)
        else ''
    )

    doc = re.sub(r'\D', '', doc_raw)

    if doc and len(doc) <= TAMANHO_CPF:
        return 'Pessoa Física'

    if 'TARIFA DOC/TED' in hist:
        return 'Tarifa'

    # O extrato traz o CNPJ sem zeros à esquerda ('4312369000190') e o
    # catálogo pode trazê-lo completo — comparar sem normalizar deixaria o
    # lançamento pendente por diferença de formatação.
    documento = normalizar_documento(doc)
    if documento in mapa_convenios:
        return mapa_convenios[documento]

    return ' Validar com a Conciliação'


def copiar_arquivo_bpo(data_alvo):
    data_nome = data_alvo.strftime('%d%m%Y')
    os.makedirs(PASTA_DESTINO_BPO, exist_ok=True)

    if not os.path.isdir(PASTA_ORIGEM_BPO):
        msg = f'Pasta de origem do BPO não encontrada: {PASTA_ORIGEM_BPO}'
        logger.error(msg)
        print(f'⚠️  {msg}')
        return

    arquivo_encontrado = None
    for nome_arquivo in os.listdir(PASTA_ORIGEM_BPO):
        match = PADRAO_NOME_BPO.match(nome_arquivo)
        if match and match.group(1) == data_nome:
            arquivo_encontrado = nome_arquivo
            break

    if not arquivo_encontrado:
        msg = f"Nenhum arquivo de conciliação encontrado para {data_alvo.strftime('%d/%m/%Y')} em {PASTA_ORIGEM_BPO}"
        logger.warning(msg)
        print(f'⚠️  {msg}')
        return

    origem = os.path.join(PASTA_ORIGEM_BPO, arquivo_encontrado)

    for antigo in glob.glob(os.path.join(PASTA_DESTINO_BPO, '*')):
        try:
            os.remove(antigo)
        except Exception as e:
            logger.error(f' Erro ao remover BPO antigo {antigo}: {e}')

    destino = os.path.join(PASTA_DESTINO_BPO, arquivo_encontrado)
    shutil.copy2(origem, destino)
    logger.info(f'Arquivo BPO copiado: {origem} -> {destino}')
    print(f'📂 Arquivo de conciliação (BPO) atualizado: {arquivo_encontrado}')


def verificar_pendencias_conciliacao(
    arquivos: list, data_str: str, mapa_convenios: dict
):
    total_geral = 0
    for arq in arquivos:
        df = ler_csv(arq, data_str, mapa_convenios)
        if df is None or df.empty or 'Convênios' not in df.columns:
            continue
        empresa = (
            df['EMPRESA'].iloc[0]
            if 'EMPRESA' in df.columns
            else 'DESCONHECIDA'
        )
        pendentes = (df['Convênios'] == 'Validar com a Conciliação').sum()
        if pendentes > 0:
            print(
                f"🔎 {empresa}: {pendentes} lançamento(s) com 'Validar com a Conciliação'."
            )
            logger.warning(
                f'[{empresa}] {pendentes} lancamento(s) pendente(s) de conciliacao.'
            )
            total_geral += pendentes

    if total_geral == 0:
        print(
            "✅ Nenhum lançamento pendente de 'Validar com a Conciliação' hoje."
        )
    else:
        print(
            f'⚠️  Total geral: {total_geral} lançamento(s) pendente(s) de conciliação hoje.'
        )


def buscar_emails_do_dia(data_alvo) -> list:
    logger.info(
        f"Buscando emails de {data_alvo.strftime('%d/%m/%Y')} do Banco Arbi"
    )

    outlook = win32com.client.Dispatch('Outlook.Application')
    namespace = outlook.GetNamespace('MAPI')
    inbox = None
    for folder in namespace.Folders:
        if EMAIL_DESTINO.lower() in folder.Name.lower():
            try:
                inbox = folder.Folders['Caixa de Entrada']
            except Exception:
                try:
                    inbox = folder.Folders['Inbox']
                except Exception:
                    pass
            break

    if inbox is None:
        for conta in namespace.Accounts:
            if conta.SmtpAddress.lower() == EMAIL_DESTINO.lower():
                raiz = namespace.Folders[conta.DisplayName]
                try:
                    inbox = raiz.Folders['Caixa de Entrada']
                except Exception:
                    try:
                        inbox = raiz.Folders['Inbox']
                    except Exception:
                        pass
                break

    if inbox is None:
        logger.error('Caixa de entrada nao encontrada!')
        print('Erro: caixa de entrada nao encontrada.')
        return []

    itens = inbox.Items
    itens.Sort('[ReceivedTime]', True)

    emails_encontrados = []
    for item in itens:
        try:
            if item.Class != 43:
                continue
            received = item.ReceivedTime
            try:
                if hasattr(received, 'astimezone'):
                    from datetime import timezone

                    received_local = received.astimezone().replace(tzinfo=None)
                else:
                    received_local = received
                data_recebido = received_local.date()
            except Exception:
                data_recebido = received.date()

            if data_recebido < data_alvo:
                from datetime import timedelta

                if data_recebido < data_alvo - timedelta(days=1):
                    break
                continue

            if data_recebido != data_alvo:
                continue

            remetente = item.SenderEmailAddress.upper().strip()
            if PADRAO_REMETENTE.match(remetente):
                emails_encontrados.append(item)
        except Exception:
            continue

    logger.info(
        f'{len(emails_encontrados)} email(s) encontrado(s) para a data.'
    )
    return emails_encontrados


def extrair_conta_do_csv(caminho_temp: str) -> str:
    try:
        with open(caminho_temp, 'r', encoding='utf-8-sig') as f:
            f.readline()
            primeira_linha = f.readline()
        conta_bruta = primeira_linha.split(';')[0]
        conta = re.sub(r'\D', '', conta_bruta).lstrip('0')
        logger.info(
            f"Conta identificada em {os.path.basename(caminho_temp)}: '{conta_bruta.strip()}' -> '{conta}'"
        )
        return conta
    except Exception as e:
        logger.error(f'Erro ao identificar conta em {caminho_temp}: {e}')
        return ''


def salvar_anexos(emails: list, data_str: str) -> list:
    pasta_dia = os.path.join(PASTA_RAIZ, data_str)
    os.makedirs(pasta_dia, exist_ok=True)
    data_nome = datetime.strptime(data_str, '%Y-%m-%d').strftime('%d-%m-%Y')
    arquivos_salvos = []
    for email in emails:
        for anexo in email.Attachments:
            nome = anexo.FileName
            if not nome.upper().endswith('.CSV'):
                continue

            temp_destino = os.path.join(pasta_dia, nome)
            anexo.SaveAsFile(temp_destino)

            conta = extrair_conta_do_csv(temp_destino)
            empresa = CONTAS.get(
                conta, f'CONTA_{conta}' if conta else 'DESCONHECIDA'
            )

            nome_final = f'{data_nome}_{empresa}.csv'
            destino_final = os.path.join(pasta_dia, nome_final)

            if os.path.exists(destino_final) and destino_final != temp_destino:
                os.remove(destino_final)
            os.rename(temp_destino, destino_final)

            logger.info(f'Anexo salvo: {destino_final}')
            arquivos_salvos.append(destino_final)

    return arquivos_salvos


def ler_csv(caminho: str, data_str: str, mapa_convenios: dict):
    try:
        df = pd.read_csv(caminho, sep=';', encoding='utf-8-sig', dtype=str)
        df = df.loc[:, ~df.columns.str.startswith('Unnamed')]
        df.columns = df.columns.str.strip()

        if 'CONTACORRENTE' in df.columns:
            conta_bruta = str(df['CONTACORRENTE'].iloc[0])
            conta = re.sub(r'\D', '', conta_bruta).lstrip('0')
            empresa_detectada = CONTAS.get(conta, f'CONTA_{conta}')
            if conta not in CONTAS:
                logger.warning(
                    f"Conta '{conta}' (bruta: '{conta_bruta}') nao encontrada em CONTAS. Usando '{empresa_detectada}'."
                )
            df.insert(0, 'EMPRESA', empresa_detectada)

        if 'CGC_CPF_CTP' in df.columns and 'HISTORICO_DESCRICAO' in df.columns:
            df['Convênios'] = df.apply(
                lambda row: classificar_convenio(
                    row.get('CGC_CPF_CTP'),
                    row.get('HISTORICO_DESCRICAO'),
                    mapa_convenios,
                ),
                axis=1,
            )

        if 'VALOR' in df.columns:
            df['VALOR'] = (
                df['VALOR']
                .astype(str)
                .str.replace('.', '', regex=False)
                .str.replace(',', '.', regex=False)
            )
            df['VALOR'] = pd.to_numeric(df['VALOR'], errors='coerce')

        if 'SALDO' in df.columns:
            df['SALDO'] = (
                df['SALDO']
                .astype(str)
                .str.replace('.', '', regex=False)
                .str.replace(',', '.', regex=False)
            )
            df['SALDO'] = pd.to_numeric(df['SALDO'], errors='coerce')

        if 'VALOR_BLOQUEADO' in df.columns:
            df['VALOR_BLOQUEADO'] = (
                df['VALOR_BLOQUEADO']
                .astype(str)
                .str.replace('.', '', regex=False)
                .str.replace(',', '.', regex=False)
            )
            df['VALOR_BLOQUEADO'] = pd.to_numeric(
                df['VALOR_BLOQUEADO'], errors='coerce'
            )

        df = normalizar_datas_movimentacao(df)

        data_fmt = datetime.strptime(data_str, '%Y-%m-%d').strftime('%d/%m/%Y')
        df['DATA_EXTRACAO'] = data_fmt
        return df
    except Exception as e:
        logger.error(f'Erro ao ler {caminho}: {e}')
        return None


def migrar_consolidados_para_subpasta():
    for empresa in set(CONTAS.values()):
        caminho_antigo = os.path.join(
            PASTA_RAIZ, f'consolidado_{empresa}.xlsx'
        )

        if not os.path.exists(caminho_antigo):
            continue

        try:
            caminho_novo = caminho_consolidado(empresa)

            df_antigo = pd.read_excel(caminho_antigo, dtype=str)
            for col_num in ('VALOR', 'SALDO', 'VALOR_BLOQUEADO'):
                if col_num in df_antigo.columns:
                    df_antigo[col_num] = pd.to_numeric(
                        df_antigo[col_num], errors='coerce'
                    )

            if os.path.exists(caminho_novo):
                df_novo = pd.read_excel(caminho_novo)
                df_final = pd.concat([df_novo, df_antigo], ignore_index=True)
            else:
                df_final = df_antigo

            df_final.to_excel(caminho_novo, index=False)
            os.remove(caminho_antigo)
            logger.info(
                f"Movido consolidado de '{empresa}' para subpasta ({len(df_antigo)} linha(s))."
            )
            print(
                f"📂 Consolidado de '{empresa}' movido para subpasta própria."
            )
        except Exception as e:
            logger.error(
                f'Erro ao mover consolidado de {empresa} para subpasta: {e}'
            )
            print(f'⚠️  Erro ao mover consolidado de {empresa}: {e}')


def migrar_nomes_underscore_para_espaco():
    nomes_antigos = {
        'ALVO_CARD': 'ALVO CARD',
        'EI_CARD': 'EI CARD',
        'VEM_BENEFICIOS': 'VEM BENEFICIOS',
        'JUNTOS_CARD': 'JUNTOS CARD',
    }

    for nome_antigo, nome_novo in nomes_antigos.items():
        caminho_antigo = os.path.join(
            PASTA_RAIZ, f'consolidado_{nome_antigo}.xlsx'
        )
        caminho_novo = os.path.join(
            PASTA_RAIZ, f'consolidado_{nome_novo}.xlsx'
        )

        if not os.path.exists(caminho_antigo):
            continue

        try:
            df_antigo = pd.read_excel(caminho_antigo, dtype=str)

            if 'EMPRESA' in df_antigo.columns:
                df_antigo['EMPRESA'] = nome_novo

            if os.path.exists(caminho_novo):
                df_novo = pd.read_excel(caminho_novo, dtype=str)
                df_final = pd.concat([df_antigo, df_novo], ignore_index=True)
            else:
                df_final = df_antigo

            df_final.to_excel(caminho_novo, index=False)
            os.remove(caminho_antigo)
            logger.info(
                f"Renomeado '{nome_antigo}' -> '{nome_novo}' ({len(df_antigo)} linha(s))."
            )
            print(
                f"🔄 Padrão de nome atualizado: '{nome_antigo}' -> '{nome_novo}'."
            )
        except Exception as e:
            logger.error(f'Erro ao migrar nome {caminho_antigo}: {e}')
            print(f'⚠️  Erro ao atualizar nome de {nome_antigo}: {e}')


def migrar_consolidados_csv_para_xlsx(mapa_convenios: dict):
    for empresa in set(CONTAS.values()):
        csv_antigo = os.path.join(PASTA_RAIZ, f'consolidado_{empresa}.csv')
        xlsx_novo = caminho_consolidado(empresa)

        if not os.path.exists(csv_antigo):
            continue

        try:
            df = pd.read_csv(
                csv_antigo, sep=';', encoding='utf-8-sig', dtype=str
            )
            df = df.loc[:, ~df.columns.str.startswith('Unnamed')]

            if 'Convênios' not in df.columns and 'CGC_CPF_CTP' in df.columns:
                col_hist = (
                    'HISTORICO_DESCRICAO'
                    if 'HISTORICO_DESCRICAO' in df.columns
                    else None
                )
                df['Convênios'] = df.apply(
                    lambda row: classificar_convenio(
                        row.get('CGC_CPF_CTP'),
                        row.get(col_hist) if col_hist else '',
                        mapa_convenios,
                    ),
                    axis=1,
                )

            for col_num in ('VALOR', 'SALDO', 'VALOR_BLOQUEADO'):
                if col_num in df.columns and df[col_num].dtype == object:
                    df[col_num] = (
                        df[col_num]
                        .astype(str)
                        .str.replace('.', '', regex=False)
                        .str.replace(',', '.', regex=False)
                    )
                    df[col_num] = pd.to_numeric(df[col_num], errors='coerce')

            if os.path.exists(xlsx_novo):
                df_existente = pd.read_excel(xlsx_novo, dtype=str)
                df_final = pd.concat(
                    [df, df_existente], ignore_index=True
                ).drop_duplicates()
            else:
                df_final = df

            df_final.to_excel(xlsx_novo, index=False)
            os.rename(csv_antigo, csv_antigo + '.migrado')
            logger.info(
                f'Migrado {csv_antigo} -> {xlsx_novo} ({len(df)} linha(s)).'
            )
            print(
                f'🔄 Histórico migrado: {empresa} ({len(df)} linha(s) de {os.path.basename(csv_antigo)}).'
            )
        except Exception as e:
            logger.error(f'Erro ao migrar {csv_antigo}: {e}')
            print(f'⚠️  Erro ao migrar histórico de {empresa}: {e}')


def normalizar_datas_movimentacao(df, coluna: str = COLUNA_DATA_MOVIMENTACAO):
    """Converte a coluna de data de movimentação para datetime real.

    Aceita as três formas que já convivem no histórico: texto brasileiro
    (DD/MM/AAAA, com ou sem hora), texto ISO (AAAA-MM-DD) e datetime. O
    formato brasileiro é convertido com máscara explícita — nunca por
    inferência — para que o dia jamais seja lido como mês. Não modifica o
    DataFrame recebido (imutabilidade).

    Args:
        df: DataFrame com os lançamentos do extrato.
        coluna: Nome da coluna de data de movimentação.

    Returns:
        Novo DataFrame com a coluna em datetime64. Valores não reconhecidos
        viram NaT e são contabilizados em log.

    Example:
        >>> import pandas as pd
        >>> entrada = pd.DataFrame({'DATA_MOVIMENTACAO': ['10/08/2026']})
        >>> normalizar_datas_movimentacao(entrada).iloc[0, 0].month
        8
    """
    if coluna not in df.columns:
        return df

    serie = df[coluna]
    texto = serie.astype(str).str.strip()
    eh_brasileira = texto.str.match(PADRAO_DATA_BR).fillna(False)

    convertida = pd.to_datetime(serie.where(~eh_brasileira), errors='coerce')

    pendentes = eh_brasileira
    for formato in FORMATOS_DATA_BR:
        if not pendentes.any():
            break
        parcial = pd.to_datetime(
            texto.where(pendentes), format=formato, errors='coerce'
        )
        convertida = convertida.fillna(parcial)
        pendentes = pendentes & convertida.isna()

    sem_movimento = convertida.dt.normalize() == DATA_SENTINELA_SEM_MOVIMENTO
    if sem_movimento.any():
        logger.info(
            f'{int(sem_movimento.sum())} linha(s) com marcador de dia sem movimentação (31/12/1899): data gravada como vazia.'
        )
        convertida = convertida.mask(sem_movimento)

    nao_convertidas = int(
        (convertida.isna() & serie.notna() & ~sem_movimento).sum()
    )
    if nao_convertidas:
        logger.warning(
            f'{nao_convertidas} valor(es) de {coluna} não reconhecido(s) como data (gravados como vazio).'
        )

    df_normalizado = df.copy()
    df_normalizado[coluna] = convertida
    return df_normalizado


def consolidado_precisa_normalizacao(
    df, coluna: str = COLUNA_DATA_MOVIMENTACAO
) -> bool:
    """Indica se a coluna de data ainda está gravada como texto.

    Args:
        df: DataFrame consolidado lido do disco.
        coluna: Nome da coluna de data de movimentação.

    Returns:
        True se a coluna existe e não é datetime — ou seja, precisa migrar.
    """
    if coluna not in df.columns:
        return False
    return not pd.api.types.is_datetime64_any_dtype(df[coluna])


def reclassificar_pendencias_consolidado(caminho, mapa_convenios: dict) -> int:
    """Reclassifica no consolidado as linhas ainda pendentes de convênio.

    Aproveita documentos que entraram no catálogo depois da captura, sem
    tocar nas linhas já classificadas. Só regrava o arquivo se houver
    mudança (idempotente).

    Args:
        caminho: Caminho do consolidado (.xlsx).
        mapa_convenios: Mapa documento -> convênio já normalizado.

    Returns:
        Quantidade de linhas reclassificadas.
    """
    df = pd.read_excel(caminho, dtype=str)
    if 'Convênios' not in df.columns:
        return 0

    pendentes = df['Convênios'].astype(str).str.strip() == PENDENTE_CONVENIO
    if not pendentes.any():
        return 0

    novos = df.loc[pendentes].apply(
        lambda linha: classificar_convenio(
            linha.get('CGC_CPF_CTP'),
            linha.get('HISTORICO_DESCRICAO'),
            mapa_convenios,
        ),
        axis=1,
    )
    mudou = novos.str.strip() != PENDENTE_CONVENIO
    if not mudou.any():
        return 0

    df_final = df.copy()
    df_final.loc[novos[mudou].index, 'Convênios'] = novos[mudou]
    df_final = normalizar_datas_movimentacao(df_final)

    try:
        df_final.to_excel(caminho, index=False)
    except OSError as e:
        # Consolidado aberto no Excel trava a escrita. Não pode derrubar a
        # execução: as demais empresas seguem e esta é retomada na próxima.
        logger.error(f'Nao foi possivel gravar {caminho}: {e}')
        print(
            f'⚠️  {os.path.basename(caminho)} está aberto/bloqueado — '
            f'{int(mudou.sum())} reclassificação(ões) não gravada(s). '
            'Feche o arquivo e execute novamente.'
        )
        return 0

    return int(mudou.sum())


def migrar_datas_consolidados() -> list:
    """Normaliza DATA_MOVIMENTACAO nos consolidados já gravados.

    Migração idempotente: só reescreve o arquivo cuja coluna ainda está em
    texto, evitando reescrever milhares de linhas a cada execução.

    Returns:
        Lista das empresas cujo consolidado foi reescrito (vazia se nenhum
        precisava de correção).
    """
    normalizados = []

    for empresa in sorted(set(CONTAS.values())):
        caminho = caminho_consolidado(empresa)
        if not os.path.exists(caminho):
            continue

        try:
            df = pd.read_excel(caminho)
            if not consolidado_precisa_normalizacao(df):
                continue

            df_final = normalizar_datas_movimentacao(df)
            df_final.to_excel(caminho, index=False)
            normalizados.append(empresa)

            logger.info(
                f'[{empresa}] DATA_MOVIMENTACAO normalizada para datetime ({len(df_final)} linha(s)).'
            )
            print(
                f'🗓️  {empresa}: datas de movimentação normalizadas ({len(df_final)} linha(s)).'
            )
        except Exception as e:
            logger.error(f'Erro ao normalizar datas de {empresa}: {e}')
            print(f'⚠️  Erro ao normalizar datas de {empresa}: {e}')

    return normalizados


def remover_linhas_da_data(df, data_fmt: str):
    """Remove do DataFrame as linhas cuja DATA_EXTRACAO casa a data informada.

    Garante idempotência do consolidado: ao reprocessar uma data, as linhas
    antigas dessa data são descartadas antes de reanexar as novas, evitando
    duplicação que geraria descasamento na conciliação. Não modifica o
    DataFrame recebido — retorna um novo (imutabilidade).

    Args:
        df: DataFrame consolidado existente.
        data_fmt: Data no formato DD/MM/AAAA (mesmo formato de DATA_EXTRACAO).

    Returns:
        DataFrame sem as linhas da data informada (ou o original se a coluna
        DATA_EXTRACAO não existir).
    """
    if 'DATA_EXTRACAO' not in df.columns:
        return df
    return df[df['DATA_EXTRACAO'].astype(str) != data_fmt]


def consolidar(arquivos: list, data_str: str, mapa_convenios: dict):
    por_empresa = {}

    for arq in arquivos:
        df = ler_csv(arq, data_str, mapa_convenios)
        if df is None or df.empty:
            continue
        empresa = (
            df['EMPRESA'].iloc[0]
            if 'EMPRESA' in df.columns
            else 'DESCONHECIDA'
        )
        por_empresa.setdefault(empresa, []).append(df)

    if not por_empresa:
        logger.warning(f'Nenhum dado para consolidar em {data_str}.')
        return

    data_fmt = datetime.strptime(data_str, '%Y-%m-%d').strftime('%d/%m/%Y')

    for empresa, frames in por_empresa.items():
        df_hoje = pd.concat(frames, ignore_index=True)
        caminho = caminho_consolidado(empresa)

        if os.path.exists(caminho):
            colunas_existentes = pd.read_excel(
                caminho, nrows=0
            ).columns.tolist()
            dtype_map = {
                col: (
                    float
                    if col in ('VALOR', 'SALDO', 'VALOR_BLOQUEADO')
                    else str
                )
                for col in colunas_existentes
            }
            dtype_map.pop(COLUNA_DATA_MOVIMENTACAO, None)
            df_existente = pd.read_excel(caminho, dtype=dtype_map)
            df_existente = normalizar_datas_movimentacao(df_existente)
            antes = len(df_existente)
            df_existente = remover_linhas_da_data(df_existente, data_fmt)
            substituidas = antes - len(df_existente)
            if substituidas:
                logger.info(
                    f'[{empresa}] {substituidas} linha(s) da data {data_fmt} substituída(s) (reprocessamento idempotente).'
                )
                print(
                    f'♻️  {empresa}: {substituidas} linha(s) de {data_fmt} substituída(s) (reprocessamento).'
                )
            df_final = pd.concat([df_existente, df_hoje], ignore_index=True)
        else:
            df_final = df_hoje

        df_final.to_excel(caminho, index=False)
        logger.info(
            f'[{empresa}] {len(df_hoje)} linha(s) da data {data_fmt}. Total: {len(df_final)} linha(s).'
        )
        print(
            f'✅ {empresa}: {len(df_hoje)} linha(s) de {data_fmt} (total acumulado: {len(df_final)}).'
        )


def catalogar_convenios_pendentes(
    simular: bool = False, consultar_externo: bool = True
) -> int:
    """Cataloga documentos pendentes e reclassifica o histórico.

    Sugestão por raiz de CNPJ entra sozinha no catálogo; sugestão por
    semelhança de nome fica na guia de revisão. Depois de catalogar, os
    consolidados são reclassificados para que o painel deixe de exibir
    'Validar com a Conciliação' nas linhas resolvidas.

    Args:
        simular: Se True, apenas relata o que seria feito.
        consultar_externo: Se True, consulta os dados públicos do CNPJ para
            os documentos sem pista. API indisponível não interrompe nada.

    Returns:
        Quantidade de lançamentos reclassificados nos consolidados.
    """
    caminhos = [
        caminho_consolidado(empresa)
        for empresa in sorted(set(CONTAS.values()))
    ]
    resultado = executar_catalogacao(
        caminhos, simular=simular, consultar_externo=consultar_externo
    )

    sufixo = ' (simulação)' if simular else ''
    print(
        f'🔖 Convênios{sufixo}: {len(resultado.pendencias)} documento(s) '
        f'pendente(s) — {len(resultado.aplicadas)} catalogado(s) '
        f'automaticamente, {len(resultado.para_revisao)} para revisão, '
        f'{len(resultado.sem_pista)} sem pista.'
    )
    for sugestao in resultado.aplicadas:
        print(
            f'   • {sugestao.documento} | '
            f'{sugestao.nome_extrato[:38]:38} -> {sugestao.convenio} '
            f'[{sugestao.origem.value}]'
        )
        if sugestao.detalhe:
            print(f'     ↳ {sugestao.detalhe}')

    if simular:
        return 0

    # Reclassifica sempre, não só quando há sugestão nova: o catálogo também
    # muda por edição manual da Conciliação e por aprovação de sugestões.
    mapa = carregar_convenios()
    reclassificados = sum(
        reclassificar_pendencias_consolidado(caminho, mapa)
        for caminho in caminhos
        if os.path.exists(caminho)
    )
    if reclassificados:
        logger.info(
            f'{reclassificados} lancamento(s) reclassificado(s) apos catalogacao.'
        )
        print(
            f'♻️  {reclassificados} lançamento(s) deixaram de ficar pendentes.'
        )

    return reclassificados


def extrato_diario_existe(data_alvo) -> bool:
    """Indica se o CSV diário de VEM BENEFICIOS da data já foi capturado.

    É o mesmo sinal usado pelo gate da importação: a presença do CSV diário
    prova que o e-mail do Banco Arbi daquela data chegou e foi salvo.

    Args:
        data_alvo: Data a verificar.

    Returns:
        True se o CSV diário de VEM BENEFICIOS da data existe em disco.
    """
    pasta_dia = os.path.join(PASTA_RAIZ, data_alvo.strftime('%Y-%m-%d'))
    if not os.path.isdir(pasta_dia):
        return False

    prefixo = data_alvo.strftime('%d-%m-%Y').upper()
    for nome in os.listdir(pasta_dia):
        nome_upper = nome.upper()
        if nome_upper.startswith(prefixo) and nome_upper.endswith(
            'VEM BENEFICIOS.CSV'
        ):
            return True
    return False


def datas_alvo_para_captura(data_referencia) -> list:
    """Monta a lista de datas a capturar: hoje e, se faltou, ontem (D-1).

    O e-mail de ontem pode não ter sido capturado (execução perdida, fim de
    semana). Nesse caso ontem entra na lista — antes de hoje — para completar
    o consolidado. A disponibilização no painel continua condicionada ao
    e-mail da data atual (regra do gate da importação).

    Args:
        data_referencia: Data atual de referência da execução.

    Returns:
        Lista de datas a processar, na ordem cronológica.
    """
    datas = [data_referencia]
    ontem = data_referencia - timedelta(days=1)
    if not extrato_diario_existe(ontem):
        datas.insert(0, ontem)
    return datas


def processar_data(data_alvo, mapa_convenios: dict) -> list:
    """Captura e-mails de uma data, salva os anexos e consolida (idempotente).

    Args:
        data_alvo: Data a processar.
        mapa_convenios: Mapa CNPJ/CPF -> convênio para classificação.

    Returns:
        Lista de caminhos dos CSV salvos para a data (vazia se sem e-mail).
    """
    data_str = data_alvo.strftime('%Y-%m-%d')

    emails = buscar_emails_do_dia(data_alvo)
    if not emails:
        msg = f"Nenhum email do Banco Arbi encontrado em {data_alvo.strftime('%d/%m/%Y')}."
        print(f'⚠️  {msg}')
        logger.warning(msg)
        return []

    arquivos = salvar_anexos(emails, data_str)
    if not arquivos:
        msg = f"Emails encontrados em {data_alvo.strftime('%d/%m/%Y')}, mas nenhum anexo CSV salvo."
        print(f'⚠️  {msg}')
        logger.warning(msg)
        return []

    print(
        f'📁 {len(arquivos)} arquivo(s) salvo(s) em: {os.path.join(PASTA_RAIZ, data_str)}'
    )
    for arq in arquivos:
        print(f'   • {os.path.basename(arq)}')

    consolidar(arquivos, data_str, mapa_convenios)
    return arquivos


def main():
    if len(sys.argv) > 1:
        try:
            data_referencia = datetime.strptime(sys.argv[1], '%d-%m-%Y').date()
        except ValueError:
            print(
                '⚠️  Data inválida. Use o formato DD-MM-AAAA, ex: 16-06-2026'
            )
            return
    else:
        data_referencia = datetime.today().date()

    print(f"\n{'='*50}")
    print(
        f"  Extração de Extratos Arbi — {data_referencia.strftime('%d/%m/%Y')}"
    )
    print(f"{'='*50}\n")

    mapa_convenios = carregar_convenios()

    migrar_nomes_underscore_para_espaco()
    migrar_consolidados_para_subpasta()
    migrar_consolidados_csv_para_xlsx(mapa_convenios)
    migrar_datas_consolidados()

    datas = datas_alvo_para_captura(data_referencia)
    if len(datas) > 1:
        retroativa = datas[0].strftime('%d/%m/%Y')
        print(
            f'ℹ️  Backfill: incluindo captura retroativa de {retroativa} (não capturada).'
        )
        logger.info(f'Backfill de {retroativa} incluído na execução.')

    arquivos_por_data = {d: processar_data(d, mapa_convenios) for d in datas}

    print(f'\n📊 Consolidados (.xlsx) salvos em: {PASTA_RAIZ}')

    arquivos_hoje = arquivos_por_data.get(data_referencia, [])
    if not arquivos_hoje:
        logger.warning(
            'Sem extrato da data atual; disponibilização não liberada (gate).'
        )
        print(f'\n📋 Log em: {log_path}\n')
        return

    logger.info('Extracao concluida com sucesso.')
    data_str = data_referencia.strftime('%Y-%m-%d')

    print()
    catalogar_convenios_pendentes()

    print()
    verificar_pendencias_conciliacao(arquivos_hoje, data_str, mapa_convenios)

    print()
    copiar_arquivo_bpo(data_referencia)

    print(f'\n📋 Log em: {log_path}\n')


if __name__ == '__main__':
    main()
