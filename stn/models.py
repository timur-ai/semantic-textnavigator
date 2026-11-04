"""Модели данных прототипа.

Значение без данных представляется как None; человекочитаемая строка
«нет данных» появляется только в слое интерфейса.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date


# Тип поля договора
FIELD_AMOUNT = "amount"      # общая стоимость, руб.
FIELD_DEADLINE = "deadline"  # срок поставки
FIELD_PENALTY = "penalty"    # ставка неустойки


@dataclass
class Evidence:
    """Проверяемое основание значения: файл, дословная цитата, абзац."""

    file: str                 # имя файла-источника
    quote: str                # дословный срез исходного текста
    para: int                 # индекс строки (абзаца) в исходном тексте, 0-based
    clause: str | None = None # номер пункта («2.1»), если применимо


@dataclass
class FieldValue:
    """Значение поля с основанием и уверенностью."""

    value: object | None          # int | date | str | None
    evidence: Evidence | None = None
    confidence: str = "high"      # high | low
    note: str | None = None       # предупреждение (например, расхождение цифры и прописи)


@dataclass
class ProposedChange:
    """Изменение условия: новая редакция в ДС или предложение в письме/проекте."""

    field: str                    # FIELD_AMOUNT | FIELD_DEADLINE
    value: object | None          # int | date
    evidence: Evidence


@dataclass
class Doc:
    """Разобранный документ."""

    kind: str                  # contract | amendment | draft_amendment | letter | unknown
    file: str                  # имя файла
    contract_no: str | None    # номер договора (свой для договора, базовый для ДС/письма)
    supplier: str | None
    buyer: str | None
    header_date: date | None   # «Дата:» в шапке
    signed_date: date | None   # дата подписания из строки статуса
    is_signed: bool | None     # True/False; None – статус не определён
    effective_date: date | None  # дата вступления в силу (для ДС)
    status_as_of: date | None  # «по состоянию на …» (для писем и проектов)
    amount: FieldValue | None  # п. 2.1 (для договора)
    deadline: FieldValue | None  # п. 3.1 (для договора)
    penalty: FieldValue | None   # п. 4.1 (для договора)
    changes: list[ProposedChange] = field(default_factory=list)  # новые редакции (ДС) или предложения (проект/письмо)
    reason: str | None = None  # причина для unknown / требует проверки


@dataclass
class ContractState:
    """Состояние договора на дату проверки."""

    contract_no: str
    supplier: str | None
    amount: FieldValue
    deadline: FieldValue
    penalty: FieldValue
    changed_by: dict[str, list[Evidence]] = field(default_factory=dict)  # поле -> цепочка изменивших документов
    # (файл, тип документа, поставщик, изменение, статус-текст)
    proposals: list[tuple[str, str, str | None, ProposedChange | None, str]] = field(default_factory=list)
    later_amendments: list[tuple[str, str]] = field(default_factory=list)  # (файл, пояснение) – подписано, но вступит позже
    requires_check: list[tuple[str, str]] = field(default_factory=list)  # (файл, причина) по связанным документам
    source_files: list[str] = field(default_factory=list)
