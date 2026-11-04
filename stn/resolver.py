"""Резолвер: сводит документы в состояние договоров на дату проверки.

Правило задания: условия изменяет только подписанное сторонами допсоглашение,
вступившее в силу к дате проверки. Письма и проекты – сведения для проверки.
"""

from __future__ import annotations

from datetime import date

from .models import (
    FIELD_AMOUNT,
    FIELD_DEADLINE,
    ContractState,
    Doc,
    FieldValue,
    ProposedChange,
)
from .parser import (
    KIND_AMENDMENT,
    KIND_CONTRACT,
    KIND_DRAFT,
    KIND_LETTER,
    KIND_UNKNOWN,
)

NO_DATA = FieldValue(None)


def _apply_change(target: FieldValue, change: ProposedChange) -> FieldValue:
    return FieldValue(value=change.value, evidence=change.evidence, confidence="high")


def build_states(docs: list[Doc], check_date: date) -> list[ContractState]:
    """Строит состояние каждого договора на дату проверки."""
    contracts = [d for d in docs if d.kind == KIND_CONTRACT]
    states: list[ContractState] = []

    for contract in contracts:
        if contract.contract_no is None:
            continue
        state = ContractState(
            contract_no=contract.contract_no,
            supplier=contract.supplier,
            amount=contract.amount or NO_DATA,
            deadline=contract.deadline or NO_DATA,
            penalty=contract.penalty or NO_DATA,
            source_files=[contract.file],
        )

        # Изменения и предложения, относящиеся к этому договору
        amendments = [
            d for d in docs
            if d.kind == KIND_AMENDMENT and d.contract_no == contract.contract_no
        ]
        others = [
            d for d in docs
            if d.kind in (KIND_DRAFT, KIND_LETTER) and d.contract_no == contract.contract_no
        ]

        # Подписанные и вступившие в силу ДС применяются по порядку
        applicable = [
            d for d in amendments
            if d.is_signed
            and d.signed_date is not None
            and d.signed_date <= check_date
            and d.effective_date is not None
            and d.effective_date <= check_date
        ]
        applicable.sort(key=lambda d: (d.effective_date, d.signed_date or d.effective_date, d.file))
        for amd in applicable:
            state.source_files.append(amd.file)
            for change in amd.changes:
                if change.field == FIELD_AMOUNT:
                    state.amount = _apply_change(state.amount, change)
                elif change.field == FIELD_DEADLINE:
                    state.deadline = _apply_change(state.deadline, change)
                state.changed_by.setdefault(change.field, []).append(change.evidence)

        # Подписанное ДС с более поздней датой вступления – вступит позже
        for amd in amendments:
            if amd in applicable:
                continue
            if amd.is_signed:
                state.later_amendments.append((
                    amd.file,
                    f"подписано {amd.signed_date.strftime('%d.%m.%Y') if amd.signed_date else 'дата неизвестна'}, "
                    f"вступает в силу {amd.effective_date.strftime('%d.%m.%Y') if amd.effective_date else 'дата неизвестна'} "
                    f"(позже даты проверки)",
                ))
            elif amd.is_signed is False:
                if amd.changes:
                    for change in amd.changes:
                        state.proposals.append((amd.file, "допсоглашение", amd.supplier, change, "допсоглашение не подписано"))
                else:
                    state.proposals.append((amd.file, "допсоглашение", amd.supplier, None, "допсоглашение не подписано"))
            else:
                state.requires_check.append((amd.file, "статус подписания не определён"))

        # Письма и проекты – несогласованные предложения
        for other in others:
            kind_word = "проект допсоглашения" if other.kind == KIND_DRAFT else "письмо поставщика"
            if not other.changes:
                state.proposals.append((other.file, kind_word, other.supplier, None, "предложение без распознанного значения"))
                continue
            status_bits = []
            if other.status_as_of:
                status_bits.append(f"по состоянию на {other.status_as_of.strftime('%d.%m.%Y')}")
            status_bits.append("проект не подписан" if other.kind == KIND_DRAFT else "согласие покупателя не получено")
            for change in other.changes:
                state.proposals.append((other.file, kind_word, other.supplier, change, ", ".join(status_bits)))

        states.append(state)

    # Нераспознанные документы – отдельным списком к каждому договору не привязываются;
    # интерфейс показывает их в общем блоке «требует проверки»
    return states


def unknown_docs(docs: list[Doc]) -> list[tuple[str, str]]:
    """Документы, которые не удалось надёжно классифицировать."""
    return [(d.file, d.reason or "не распознан") for d in docs if d.kind == KIND_UNKNOWN]


def orphans(docs: list[Doc], states: list[ContractState]) -> list[tuple[str, str]]:
    """ДС/письма/проекты, не привязанные ни к одному договору."""
    known = {s.contract_no for s in states}
    out = []
    for d in docs:
        if d.kind in (KIND_AMENDMENT, KIND_DRAFT, KIND_LETTER) and (
            d.contract_no is None or d.contract_no not in known
        ):
            out.append((d.file, f"не найден базовый договор {d.contract_no or '—'}"))
    return out
