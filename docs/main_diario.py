# INSERIR EM: automação_email_antigo/main_diario.py
#
# Ponto de entrada único do processo diário. Encadeia, em ordem:
#   1. extrair_extratos.py   -> produz os insumos (e-mails Arbi + BPO)
#   2. importar_extrato_web.py -> valida o gate e importa no painel web
#
# Cada etapa roda como subprocesso isolado (processo próprio, logging
# próprio, exit code próprio). Isso evita a colisão de `logging.basicConfig`
# e os efeitos colaterais de import do extrair_extratos.py, e dá o código de
# saída de cada etapa para o controle de fluxo.

# --- stdlib ---
import logging
import subprocess
import sys
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Optional, Sequence

# --- locais ---
from checkpoint_importacao import importacao_ja_concluida

# --------------------------------------------------------------------------- #
# Constantes
# --------------------------------------------------------------------------- #
SCRIPT_EXTRACAO = Path(__file__).with_name('extrair_extratos.py')
SCRIPT_IMPORTACAO = Path(__file__).with_name('importar_extrato_web.py')

# Definido localmente (sem importar o módulo de importação) para manter o
# orquestrador desacoplado de py_rpautom — ele apenas dispara subprocessos.
PASTA_RAIZ = Path(r'C:\RPA\automação_email_antigo\extratos')
CAMINHO_LOG_DIARIO = PASTA_RAIZ / 'log_diario.txt'

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Dataclass (imutável)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class ResultadoEtapa:
    """Resultado de uma etapa executada como subprocesso."""

    nome: str
    codigo: int

    @property
    def sucesso(self) -> bool:
        """True quando o subprocesso terminou com código 0."""
        return self.codigo == 0


# --------------------------------------------------------------------------- #
# Funções puras — decisão de fluxo
# --------------------------------------------------------------------------- #
def deve_prosseguir_para_importacao(extracao: ResultadoEtapa) -> bool:
    """Decide se a importação deve rodar após a extração.

    A extração termina em código 0 mesmo quando "não há nada a fazer"
    (ex.: sem e-mails do dia); nesses casos o gate da importação decide.
    Só interrompe o encadeamento se a extração falhar (código != 0).

    Args:
        extracao: Resultado da etapa de extração.

    Returns:
        True se a importação deve ser executada em seguida.
    """
    return extracao.sucesso


def resolver_data_referencia(argumentos: Sequence[str]) -> Optional[date]:
    """Resolve a data de referência a partir dos argumentos de linha.

    Args:
        argumentos: ``argv[1:]``; primeiro item opcional em DD-MM-AAAA.

    Returns:
        Data informada, a data de hoje se nenhum argumento for passado,
        ou None se o argumento não estiver no formato DD-MM-AAAA (deixa a
        etapa de extração emitir a mensagem de erro apropriada).
    """
    if not argumentos:
        return date.today()
    try:
        return datetime.strptime(argumentos[0], '%d-%m-%Y').date()
    except ValueError:
        return None


def consolidar_codigo_saida(
    extracao: ResultadoEtapa,
    importacao: Optional[ResultadoEtapa],
) -> int:
    """Consolida o código de saída final do fluxo diário.

    Args:
        extracao: Resultado da extração.
        importacao: Resultado da importação, ou None se ela não rodou.

    Returns:
        Código de saída do fluxo (0 = sucesso).

    Example:
        >>> e = ResultadoEtapa('extração', 0)
        >>> consolidar_codigo_saida(e, ResultadoEtapa('importação', 1))
        1
    """
    if importacao is None:
        return extracao.codigo
    return importacao.codigo


# --------------------------------------------------------------------------- #
# I/O — execução de subprocessos e orquestração
# --------------------------------------------------------------------------- #
def executar_script(
    caminho: Path,
    argumentos: Sequence[str] = (),
) -> int:
    """Executa um script Python como subprocesso, com saída ao vivo.

    Args:
        caminho: Caminho do script a executar.
        argumentos: Argumentos de linha de comando repassados ao script.

    Returns:
        Código de saída do subprocesso.
    """
    comando = [sys.executable, str(caminho), *argumentos]
    logger.info('Executando: %s', ' '.join(comando))
    processo = subprocess.run(comando, cwd=str(caminho.parent))
    return processo.returncode


def executar_fluxo_diario(argumentos_extracao: Sequence[str] = ()) -> int:
    """Orquestra extração -> (gate) -> importação.

    Args:
        argumentos_extracao: Argumentos repassados ao extrair_extratos.py
            (ex.: data ``DD-MM-AAAA``).

    Returns:
        Código de saída final do fluxo.
    """
    extracao = ResultadoEtapa(
        'extração', executar_script(SCRIPT_EXTRACAO, argumentos_extracao)
    )
    logger.info('Extração terminou com código %s.', extracao.codigo)

    if not deve_prosseguir_para_importacao(extracao):
        logger.critical(
            'Extração falhou (código %s). Importação NÃO será executada.',
            extracao.codigo,
        )
        return consolidar_codigo_saida(extracao, None)

    # Encadeia os mesmos argumentos (ex.: data DD-MM-AAAA) para que o gate
    # do e-mail Arbi do dia use a mesma data de referência da extração.
    importacao = ResultadoEtapa(
        'importação', executar_script(SCRIPT_IMPORTACAO, argumentos_extracao)
    )
    logger.info('Importação terminou com código %s.', importacao.codigo)
    return consolidar_codigo_saida(extracao, importacao)


def configurar_logging() -> None:
    """Configura logging do orquestrador para arquivo e console."""
    PASTA_RAIZ.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] [DIARIO] %(message)s',
        datefmt='%d/%m/%Y %H:%M:%S',
        handlers=[
            logging.FileHandler(CAMINHO_LOG_DIARIO, encoding='utf-8'),
            logging.StreamHandler(sys.stdout),
        ],
    )


def main() -> int:
    """Ponto de entrada do processo diário.

    Returns:
        Código de saída: 0 sucesso · 1 gate abortou · 2 erro na automação
        · código da extração se ela falhar.
    """
    configurar_logging()
    logger.info('=== Fluxo diário: extração -> importação ===')
    argumentos_extracao = tuple(sys.argv[1:])

    data_referencia = resolver_data_referencia(argumentos_extracao)
    if data_referencia is not None and importacao_ja_concluida(
        data_referencia
    ):
        logger.info(
            'Importação de %s já concluída com sucesso; interrompendo o '
            'fluxo (nada a reprocessar).',
            data_referencia.strftime('%d/%m/%Y'),
        )
        return 0

    codigo = executar_fluxo_diario(argumentos_extracao)
    logger.info('Fluxo diário finalizado com código %s.', codigo)
    return codigo


if __name__ == '__main__':
    sys.exit(main())
