# INSERIR EM: automação_email_antigo/tests/test_main_diario.py
# DEPENDÊNCIA: pip install pytest

# --- stdlib ---
from datetime import date

# --- locais ---
from main_diario import resolver_data_referencia


def test_resolver_data_sem_argumento_usa_hoje():
    assert resolver_data_referencia(()) == date.today()


def test_resolver_data_com_argumento_valido():
    assert resolver_data_referencia(('05-08-2026',)) == date(2026, 8, 5)


def test_resolver_data_com_argumento_invalido_retorna_none():
    assert resolver_data_referencia(('2026-08-05',)) is None
