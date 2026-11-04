"""Стресс-тест «встречи»: новый договор того же формата с изменённым полем.

Проверяет, что логика не привязана к номерам договоров, именам файлов
и эталонным значениям: STN-104 обрабатывается теми же правилами.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from stn.parser import parse_document
from stn.qa import field_answer
from stn.resolver import build_states

from conftest import SAMPLES

FIXTURES = Path(__file__).parent / "fixtures"


def docs_with_fixtures():
    docs = [parse_document(p.name, p.read_text(encoding="utf-8")) for p in sorted(SAMPLES.glob("*.txt"))]
    for p in sorted(FIXTURES.glob("*.txt")):
        docs.append(parse_document(p.name, p.read_text(encoding="utf-8")))
    return docs


def test_new_contract_extracted(check_date):
    doc = parse_document(
        "07_contract_new.txt", (FIXTURES / "07_contract_new.txt").read_text(encoding="utf-8")
    )
    assert doc.contract_no == "STN-104"
    assert doc.supplier == "ООО «Тестовое Кольцо»"
    assert doc.amount.value == 2_150_000          # ловушка-аванс 430 000 проигнорирована
    assert doc.deadline.value == date(2026, 5, 15)  # словесная дата «15 мая 2026 года»
    assert doc.deadline.confidence == "high"
    assert "30.06.2026" not in (doc.deadline.evidence.quote if doc.deadline.evidence else "")
    assert "0,08%" in (doc.penalty.value or "")


def test_amended_value_on_new_contract(check_date):
    states = build_states(docs_with_fixtures(), check_date)
    s104 = next(s for s in states if s.contract_no == "STN-104")
    # подписанное ДС в силе с 05.03.2026 <= 10.03.2026 -> новая сумма
    assert s104.amount.value == 2_300_000
    assert s104.amount.evidence.file == "08_amendment_signed_104.txt"
    # срок не менялся: словесная дата из договора
    assert s104.deadline.value == date(2026, 5, 15)
    assert s104.deadline.evidence.file == "07_contract_new.txt"
    assert "15 мая 2026" in s104.deadline.evidence.quote


def test_answer_for_new_contract(check_date):
    states = build_states(docs_with_fixtures(), check_date)
    answer = field_answer(states, "STN-104", "amount")
    assert "2 300 000" in answer
    assert "08_amendment_signed_104.txt" in answer


def test_no_hardcoded_references_in_source():
    """В коде домена (не в комментариях) нет номеров договоров и имён файлов-образцов."""
    import re as _re
    pkg = Path(__file__).parent.parent / "stn"
    pattern = _re.compile(r"STN-10[0-9]|0[1-6]_[a-z]+\.txt")
    for py in sorted(pkg.glob("*.py")):
        for lineno, line in enumerate(py.read_text(encoding="utf-8").splitlines(), 1):
            code = line.split("#", 1)[0]  # комментарии не считаются
            assert not pattern.search(code), f"вшитая ссылка: {py.name}:{lineno}: {line.strip()}"
