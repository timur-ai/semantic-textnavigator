"""Эталонные ответы на четыре вопроса пользователя (на учебном наборе)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from stn.qa import delivery_before, field_answer, fmt_value
from stn.parser import parse_document
from stn.resolver import build_states

from conftest import SAMPLES


def build(check: date):
    docs = [parse_document(p.name, p.read_text(encoding="utf-8")) for p in sorted(SAMPLES.glob("*.txt"))]
    return build_states(docs, check)


# Вопрос 1: срок поставки по STN-101 на 10.03.2026
def test_q1_stn101_deadline(check_date):
    answer = field_answer(build(check_date), "STN-101", "deadline")
    assert "30.04.2026" in answer
    assert "02_amendment_signed.txt" in answer      # основание – подписанное ДС
    assert "изменено" in answer.lower()


# Вопрос 2: сумма STN-102
def test_q2_stn102_amount(check_date):
    answer = field_answer(build(check_date), "STN-102", "amount")
    assert "840 000" in answer
    assert "03_contract.txt" in answer


# Вопрос 3: сумма STN-103 – нет данных, без выдумывания
def test_q3_stn103_amount_is_no_data(check_date):
    answer = field_answer(build(check_date), "STN-103", "amount")
    assert "нет данных" in answer


# Вопрос 4: раньше 01.04.2026 + несогласованные изменения срока (дословный эталон)
def test_q4_before_april_unsolicited(check_date):
    answer = delivery_before(build(check_date), date(2026, 4, 1))
    assert "Раньше 01.04.2026 поставка запланирована только по STN-102" in answer
    assert "20.03.2026" in answer
    # оба несогласованных изменения срока: письмо STN-102 и проект ДС №2 STN-101
    assert "письмо поставщика ООО «Контур Электро» по STN-102" in answer
    assert "27.03.2026" in answer
    assert "проект допсоглашения" in answer and "STN-101" in answer
    assert "10.05.2026" in answer
    # STN-101 и STN-103 (30.04.2026) не попадают в «раньше»
    assert "только по STN-102" in answer


# Дополнительные контрактные проверки формата ответа
def test_fmt_value_variants():
    from stn.models import FieldValue
    assert fmt_value(FieldValue(None)) == "нет данных"
    assert fmt_value(FieldValue(1_380_000)) == "1 380 000 руб."
    assert fmt_value(FieldValue(date(2026, 4, 30))) == "не позднее 30.04.2026"


def test_question_params_not_hardcoded(check_date):
    """Вопросы параметризованы: другой порог и номер дают корректный другой ответ."""
    answer = field_answer(build(check_date), "STN-102", "deadline")
    assert "20.03.2026" in answer
    answer2 = delivery_before(build(check_date), date(2026, 5, 1))
    # при пороге 01.05.2026 в «раньше» попадают все три договора
    for no in ("STN-101", "STN-102", "STN-103"):
        assert no in answer2
