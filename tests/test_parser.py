"""Юнит-тесты парсера: классификация, извлечение, ловушки, дословные цитаты."""

from __future__ import annotations

from datetime import date

from stn.parser import (
    KIND_AMENDMENT,
    KIND_CONTRACT,
    KIND_DRAFT,
    KIND_LETTER,
    KIND_UNKNOWN,
    parse_document,
    split_clauses,
    words_to_number,
)

from conftest import load_sample


# ---------- классификация ----------

def test_classifier_contract():
    doc = parse_document("01_contract.txt", load_sample("01_contract.txt"))
    assert doc.kind == KIND_CONTRACT
    assert doc.contract_no == "STN-101"
    assert doc.is_signed is True
    assert doc.signed_date == date(2026, 2, 5)


def test_classifier_signed_amendment():
    doc = parse_document("02_amendment_signed.txt", load_sample("02_amendment_signed.txt"))
    assert doc.kind == KIND_AMENDMENT
    assert doc.contract_no == "STN-101"  # «К ДОГОВОРУ STN-101» без «№»
    assert doc.is_signed is True
    assert doc.effective_date == date(2026, 3, 1)


def test_classifier_draft_before_amendment():
    """«ПРОЕКТ ДОПОЛНИТЕЛЬНОГО СОГЛАШЕНИЯ» не должен классифицироваться как ДС."""
    doc = parse_document("06_amendment_draft.txt", load_sample("06_amendment_draft.txt"))
    assert doc.kind == KIND_DRAFT
    assert doc.is_signed is False
    assert doc.status_as_of == date(2026, 3, 10)


def test_classifier_letter():
    doc = parse_document("05_supplier_email.txt", load_sample("05_supplier_email.txt"))
    assert doc.kind == KIND_LETTER
    assert doc.contract_no == "STN-102"  # падежная форма «по договору STN-102»
    assert doc.supplier == "ООО «Контур Электро»"
    assert doc.status_as_of == date(2026, 3, 10)


def test_classifier_unknown():
    doc = parse_document("random.txt", "Какой-то произвольный текст без заголовка.")
    assert doc.kind == KIND_UNKNOWN
    assert doc.reason


# ---------- извлечение полей договора ----------

def test_contract_stn101_amount_and_deadline():
    doc = parse_document("01_contract.txt", load_sample("01_contract.txt"))
    assert doc.amount.value == 1_200_000
    assert doc.amount.confidence == "high"
    assert doc.deadline.value == date(2026, 4, 15)
    # дословная цитата содержит цифры и пропись
    assert "1 200 000" in doc.amount.evidence.quote
    assert "один миллион двести тысяч" in doc.amount.evidence.quote
    assert doc.deadline.evidence.quote == "15.04.2026"
    assert doc.deadline.evidence.clause == "3.1"


def test_contract_stn101_penalty():
    doc = parse_document("01_contract.txt", load_sample("01_contract.txt"))
    assert doc.penalty is not None
    assert "0,1%" in doc.penalty.value and "10%" in doc.penalty.value
    assert "0,1%" in doc.penalty.evidence.quote
    assert doc.penalty.evidence.clause == "4.1"


def test_traps_stn102_advance_and_payment_not_confused():
    doc = parse_document("03_contract.txt", load_sample("03_contract.txt"))
    assert doc.amount.value == 840_000          # аванс 252 000 не подменяет сумму
    assert doc.deadline.value == date(2026, 3, 20)  # срок оплаты 10.04.2026 не подменяет срок


def test_traps_stn103_limit_is_not_amount():
    doc = parse_document("04_contract.txt", load_sample("04_contract.txt"))
    assert doc.amount.value is None            # лимит 600 000 и ссылка на спецификацию
    assert doc.deadline.value == date(2026, 4, 30)
    assert doc.penalty.value is None          # «ставка неустойки не установлена»


# ---------- изменения и предложения ----------

def test_signed_amendment_new_wording():
    doc = parse_document("02_amendment_signed.txt", load_sample("02_amendment_signed.txt"))
    fields = {ch.field: ch for ch in doc.changes}
    assert fields["amount"].value == 1_380_000
    assert "1 380 000" in fields["amount"].evidence.quote
    assert fields["deadline"].value == date(2026, 4, 30)
    assert fields["deadline"].evidence.quote == "30.04.2026"


def test_letter_proposal_transfer():
    doc = parse_document("05_supplier_email.txt", load_sample("05_supplier_email.txt"))
    assert len(doc.changes) == 1
    ch = doc.changes[0]
    assert ch.field == "deadline"
    assert ch.value == date(2026, 3, 27)       # «на 27.03.2026», а не «с 20.03.2026»
    assert ch.evidence.quote == "27.03.2026"


def test_draft_proposals_amount_and_deadline():
    doc = parse_document("06_amendment_draft.txt", load_sample("06_amendment_draft.txt"))
    fields = {ch.field: ch.value for ch in doc.changes}
    assert fields["amount"] == 1_450_000       # не распознаётся как срок
    assert fields["deadline"] == date(2026, 5, 10)  # не распознаётся как сумма 10


# ---------- вспомогательные механизмы ----------

def test_split_clauses_finds_numbered_points():
    text = load_sample("01_contract.txt")
    clauses = split_clauses(text)
    for no in ("1.1", "1.2", "2.1", "2.2", "3.1", "3.2", "4.1", "5.1"):
        assert no in clauses, f"пункт {no} не найден"


def test_words_to_number():
    assert words_to_number("один миллион двести тысяч") == 1_200_000
    assert words_to_number("восемьсот сорок тысяч") == 840_000
    assert words_to_number("два миллиона сто пятьдесят тысяч") == 2_150_000


def test_amount_digits_words_mismatch_gives_no_data():
    text = (
        "ДОГОВОР ПОСТАВКИ № STN-900\nДата: 01.02.2026\nСтатус: подписан обеими сторонами 01.02.2026.\n\n"
        "2.1. Общая стоимость поставки составляет 500 000 (один миллион) рублей.\n"
        "3.1. Поставщик обязуется поставить товар не позднее 01.03.2026.\n"
    )
    doc = parse_document("mismatch.txt", text)
    assert doc.amount.value is None
    assert doc.amount.note and "расхождение" in doc.amount.note


def test_verbal_month_date():
    text = (
        "ДОГОВОР ПОСТАВКИ № STN-901\nДата: 01.02.2026\nСтатус: подписан обеими сторонами 01.02.2026.\n\n"
        "2.1. Общая стоимость поставки составляет 100 000 рублей.\n"
        "3.1. Поставщик обязуется поставить товар не позднее 15 мая 2026 года.\n"
    )
    doc = parse_document("verbal.txt", text)
    assert doc.deadline.value == date(2026, 5, 15)


def test_tier_b_semantic_fallback_low_confidence():
    """Договор без нумерованных пунктов: ярус Б извлекает с низкой уверенностью."""
    text = (
        "ДОГОВОР ПОСТАВКИ № STN-902\nДата: 01.02.2026\nСтатус: подписан обеими сторонами 01.02.2026.\n\n"
        "Общая стоимость поставки составляет 750 000 рублей. "
        "Поставщик обязуется поставить оборудование не позднее 20.05.2026.\n"
    )
    doc = parse_document("semantic.txt", text)
    assert doc.amount.value == 750_000
    assert doc.amount.confidence == "low"
    assert doc.deadline.value == date(2026, 5, 20)
    assert doc.deadline.confidence == "low"


def test_tier_b_negative_context_stays_no_data():
    """«Общая стоимость определяется спецификацией» не должна давать число ярусом Б."""
    text = (
        "ДОГОВОР ПОСТАВКИ № STN-903\nДата: 01.02.2026\nСтатус: подписан обеими сторонами 01.02.2026.\n\n"
        "Общая стоимость определяется спецификацией, числовое значение не указано. "
        "Поставщик обязуется поставить оборудование не позднее 20.05.2026.\n"
    )
    doc = parse_document("negative.txt", text)
    assert doc.amount.value is None
