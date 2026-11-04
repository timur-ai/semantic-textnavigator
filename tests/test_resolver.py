"""Тесты резолвера: применимость изменений, порядок, цепочки оснований."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from stn.parser import parse_document
from stn.resolver import build_states, orphans, unknown_docs

from conftest import SAMPLES


def all_docs(extra: list[Path] | None = None):
    docs = [parse_document(p.name, p.read_text(encoding="utf-8")) for p in sorted(SAMPLES.glob("*.txt"))]
    for p in extra or []:
        docs.append(parse_document(p.name, p.read_text(encoding="utf-8")))
    return docs


def state_for(states, no):
    return next(s for s in states if s.contract_no == no)


# ---------- основной сценарий ----------

def test_states_on_check_date_2026_03_10(check_date):
    states = build_states(all_docs(), check_date)
    s101 = state_for(states, "STN-101")
    s102 = state_for(states, "STN-102")
    s103 = state_for(states, "STN-103")
    # ДС №1 подписано и в силе с 01.03.2026 <= 10.03.2026 -> применяется
    assert s101.amount.value == 1_380_000
    assert s101.deadline.value == date(2026, 4, 30)
    assert s101.supplier == "ООО «Вектор Прибор»"
    # основание суммы – допсоглашение, не договор
    assert s101.amount.evidence.file == "02_amendment_signed.txt"
    chain = s101.changed_by["amount"]
    assert [e.file for e in chain] == ["02_amendment_signed.txt"]
    # письмо и проект не меняют значений
    assert s102.amount.value == 840_000
    assert s102.deadline.value == date(2026, 3, 20)
    assert s103.amount.value is None
    assert s103.deadline.value == date(2026, 4, 30)


def test_before_amendment_check_date(check_date):
    """На дату до вступления ДС в силу действуют исходные условия договора."""
    states = build_states(all_docs(), date(2026, 2, 28))
    s101 = state_for(states, "STN-101")
    assert s101.amount.value == 1_200_000
    assert s101.deadline.value == date(2026, 4, 15)
    assert s101.amount.evidence.file == "01_contract.txt"


def test_unsolicited_proposals_collected(check_date):
    states = build_states(all_docs(), check_date)
    s101 = state_for(states, "STN-101")
    s102 = state_for(states, "STN-102")
    p101 = {(kind, ch.field if ch else None, str(ch.value) if ch else None) for f, kind, sup, ch, st in s101.proposals}
    assert ("проект допсоглашения", "amount", "1450000") in p101
    assert ("проект допсоглашения", "deadline", "2026-05-10") in p101
    p102 = [(ch.field, ch.value) for f, kind, sup, ch, st in s102.proposals if ch]
    assert ("deadline", date(2026, 3, 27)) in p102
    # статусы содержат дату «по состоянию на»
    assert all("по состоянию на 10.03.2026" in st for *_ , st in s101.proposals + s102.proposals if st)


# ---------- границы применимости ----------

def test_backdated_signature_not_applied(check_date):
    """ДС, подписанное ПОСЛЕ даты проверки, не применяется даже с обратной датой вступления."""
    text = (
        "ДОПОЛНИТЕЛЬНОЕ СОГЛАШЕНИЕ № 1 К ДОГОВОРУ STN-102\n"
        "Дата: 15.03.2026\nСтатус: подписано обеими сторонами 15.03.2026.\n\n"
        "1. Пункт 2.1 договора изложить в следующей редакции: «Общая стоимость поставки составляет 900 000 рублей».\n"
        "2. Настоящее соглашение вступает в силу 01.03.2026.\n"
    )
    doc = parse_document("backdated.txt", text)
    states = build_states(all_docs() + [doc], check_date)
    s102 = state_for(states, "STN-102")
    assert s102.amount.value == 840_000          # не изменилось
    assert any("вступит позже" not in note and True for f, note in s102.later_amendments) or s102.later_amendments
    # пояснение содержит слово «позже»
    assert "позже" in s102.later_amendments[0][1]


def test_effective_after_check_shown_as_later(check_date):
    text = (
        "ДОПОЛНИТЕЛЬНОЕ СОГЛАШЕНИЕ № 2 К ДОГОВОРУ STN-103\n"
        "Дата: 05.03.2026\nСтатус: подписано обеими сторонами 05.03.2026.\n\n"
        "1. Пункт 3.1 договора изложить в следующей редакции: «Поставщик обязуется поставить запасные части не позднее 30.05.2026».\n"
        "2. Настоящее соглашение вступает в силу 01.04.2026.\n"
    )
    doc = parse_document("later.txt", text)
    states = build_states(all_docs() + [doc], check_date)
    s103 = state_for(states, "STN-103")
    assert s103.deadline.value == date(2026, 4, 30)  # не изменилось
    assert len(s103.later_amendments) == 1
    assert "later.txt" == s103.later_amendments[0][0]


def test_multiple_amendments_last_in_force_wins(check_date):
    """Два подписанных ДС: применяются по порядку, последнее в силе выигрывает."""
    base = all_docs()
    d1 = parse_document("amd_a.txt", (
        "ДОПОЛНИТЕЛЬНОЕ СОГЛАШЕНИЕ № 1 К ДОГОВОРУ STN-103\n"
        "Дата: 01.03.2026\nСтатус: подписано обеими сторонами 01.03.2026.\n\n"
        "1. Пункт 2.1 договора изложить в следующей редакции: «Общая стоимость поставки составляет 400 000 рублей».\n"
        "2. Настоящее соглашение вступает в силу 01.03.2026.\n"
    ))
    d2 = parse_document("amd_b.txt", (
        "ДОПОЛНИТЕЛЬНОЕ СОГЛАШЕНИЕ № 2 К ДОГОВОРУ STN-103\n"
        "Дата: 05.03.2026\nСтатус: подписано обеими сторонами 05.03.2026.\n\n"
        "1. Пункт 2.1 договора изложить в следующей редакции: «Общая стоимость поставки составляет 450 000 рублей».\n"
        "2. Настоящее соглашение вступает в силу 05.03.2026.\n"
    ))
    states = build_states(base + [d1, d2], check_date)
    s103 = state_for(states, "STN-103")
    assert s103.amount.value == 450_000
    assert [e.file for e in s103.changed_by["amount"]] == ["amd_a.txt", "amd_b.txt"]


# ---------- ненадёжные ситуации ----------

def test_unknown_and_orphans(check_date):
    garbage = parse_document("garbage.txt", "Случайный текст без реквизитов.")
    orphan = parse_document("orphan.txt", (
        "ДОПОЛНИТЕЛЬНОЕ СОГЛАШЕНИЕ № 9 К ДОГОВОРУ STN-999\n"
        "Дата: 01.03.2026\nСтатус: подписано обеими сторонами 01.03.2026.\n"
        "1. Пункт 2.1 договора изложить в следующей редакции: «Общая стоимость поставки составляет 100 000 рублей».\n"
    ))
    docs = all_docs() + [garbage, orphan]
    assert unknown_docs(docs) == [("garbage.txt", "заголовок документа не распознан")]
    assert orphans(docs, build_states(docs, check_date)) == [
        ("orphan.txt", "не найден базовый договор STN-999")
    ]
