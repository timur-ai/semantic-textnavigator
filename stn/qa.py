"""Ответы на вопросы пользователя: значения с проверяемыми основаниями.

Номера договоров, даты и пороги – параметры функций; эталонные ответы
нигде не зашиты (см. tests/test_qa.py).
"""

from __future__ import annotations

from datetime import date

from .models import ContractState, Evidence, FieldValue

FIELD_TITLES = {"amount": "сумма", "deadline": "срок поставки", "penalty": "неустойка"}


def fmt_value(fv: FieldValue) -> str:
    """Человекочитаемое значение поля; None -> «нет данных»."""
    if fv.value is None:
        return "нет данных"
    if isinstance(fv.value, date):
        return f"не позднее {fv.value.strftime('%d.%m.%Y')}"
    if isinstance(fv.value, int):
        return f"{fv.value:,} руб.".replace(",", " ")
    return str(fv.value)


def fmt_evidence(evidence: Evidence | None) -> str:
    if evidence is None:
        return "основание не найдено – требует проверки"
    where = f", пункт {evidence.clause}" if evidence.clause else ""
    return f"файл «{evidence.file}»{where}, абзац {evidence.para + 1}: «{evidence.quote}»"


def _state_for(states: list[ContractState], contract_no: str) -> ContractState | None:
    return next((s for s in states if s.contract_no == contract_no), None)


def field_answer(
    states: list[ContractState], contract_no: str, field_name: str
) -> str:
    """Ответ на вопрос о значении поля договора: значение + основание + кем изменено."""
    state = _state_for(states, contract_no)
    if state is None:
        return f"Договор {contract_no} не найден среди загруженных документов."
    fv: FieldValue = getattr(state, field_name)
    title = FIELD_TITLES.get(field_name, field_name)
    lines = [f"{title.capitalize()} по договору {contract_no}: {fmt_value(fv)}."]
    chain = state.changed_by.get(field_name, [])
    if chain:
        changed = " → ".join(ev.file for ev in chain)
        lines.append(f"Значение изменено документом: {changed}.")
    lines.append(f"Основание: {fmt_evidence(fv.evidence)}.")
    if fv.confidence == "low":
        lines.append("Внимание: низкая уверенность извлечения – требует проверки.")
    if fv.note:
        lines.append(f"Внимание: {fv.note}.")
    return "\n".join(lines)


def delivery_before(
    states: list[ContractState], before: date
) -> str:
    """Договоры со сроком поставки раньше порога + все несогласованные изменения срока."""
    hits = [
        s for s in states
        if s.deadline.value is not None and s.deadline.value < before
    ]
    if hits:
        head = (
            f"Раньше {before.strftime('%d.%m.%Y')} поставка запланирована только по "
            + "; ".join(
                f"{s.contract_no} – {fmt_value(s.deadline)}"
                + (f" (изменено: {' → '.join(e.file for e in s.changed_by.get('deadline', []))})" if s.changed_by.get("deadline") else "")
                for s in hits
            )
            + "."
        )
    else:
        head = f"Раньше {before.strftime('%d.%m.%Y')} поставка не запланирована ни по одному договору."

    # Несогласованные изменения срока по ВСЕМ договорам (вопрос без привязки к первой части)
    proposal_lines: list[str] = []
    for state in states:
        for file, kind_word, supplier, change, status in state.proposals:
            if change is not None and change.field == "deadline":
                who = f"{kind_word} {supplier}" if supplier else kind_word
                proposal_lines.append(
                    f"{who} по {state.contract_no} (предложение переноса на "
                    f"{change.value.strftime('%d.%m.%Y')}, {status}); "
                    f"цитата: «{change.evidence.quote}», файл «{change.evidence.file}»"
                )
            elif change is None and "не подписано" in status:
                proposal_lines.append(f"{kind_word} «{file}» по {state.contract_no}: {status}")
    if proposal_lines:
        head += " Несогласованные изменения срока: " + "; ".join(proposal_lines) + "."
    else:
        head += " Несогласованных изменений срока нет."
    return head
