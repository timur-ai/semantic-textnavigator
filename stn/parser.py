"""Разбор документов: классификация и извлечение полей с дословными основаниями.

Двухъярусная схема:
- ярус А – регулярные выражения внутри тел нумерованных пунктов (2.1, 3.1, 4.1);
- ярус Б – семантический фолбэк по ключевым фразам, включается только когда
  ярус А вернул None; результат помечается confidence="low".
"""

from __future__ import annotations

import re
from datetime import date

from .models import (
    FIELD_AMOUNT,
    FIELD_DEADLINE,
    FIELD_PENALTY,
    Doc,
    Evidence,
    FieldValue,
    ProposedChange,
)

KIND_CONTRACT = "contract"
KIND_AMENDMENT = "amendment"
KIND_DRAFT = "draft_amendment"
KIND_LETTER = "letter"
KIND_UNKNOWN = "unknown"

MONTHS = {
    "января": 1, "февраля": 2, "марта": 3, "апреля": 4,
    "мая": 5, "июня": 6, "июля": 7, "августа": 8,
    "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12,
}

DATE_NUM_RE = re.compile(r"(\d{2})\.(\d{2})\.(\d{4})")
DATE_WORDS_RE = re.compile(r"(\d{1,2})\s+([а-яё]+)\s+(\d{4})\s*(?:г\.|года)?", re.IGNORECASE)

# Номер договора вида STN-101 (латиница или кириллица + дефис + цифры)
CONTRACT_NO_TOKEN_RE = re.compile(r"\b([A-ZА-Я]{2,}-\d{1,6})\b")
# Ссылка на договор в падежной форме: «К ДОГОВОРУ STN-101», «по договору STN-102»
CONTRACT_REF_RE = re.compile(
    r"договор[ауе]?\s*(?:№\s*)?([A-ZА-Я]{2,}-\d{1,6})\b", re.IGNORECASE
)

AMOUNT_DIGITS_RE = re.compile(r"(\d{1,3}(?:[\s\u00a0]\d{3})+|\d{4,})")
AMOUNT_WORDS_RE = re.compile(r"[((]?\s*((?:[а-яё]+\s+){1,8}(?:тысяч[аиы]?|миллион[аов]?|миллиард[аов]?))\s*[).]", re.IGNORECASE)
RUBLE_HINT_RE = re.compile(r"рубл(?:ей|я|ю|ём|ей\.)")

UNITS = {
    "один": 1, "два": 2, "три": 3, "четыре": 4, "пять": 5, "шесть": 6,
    "семь": 7, "восемь": 8, "девять": 9, "десять": 10, "одиннадцать": 11,
    "двенадцать": 12, "тринадцать": 13, "четырнадцать": 14, "пятнадцать": 15,
    "шестнадцать": 16, "семнадцать": 17, "восемнадцать": 18, "девятнадцать": 19,
    "двадцать": 20, "тридцать": 30, "сорок": 40, "пятьдесят": 50, "шестьдесят": 60,
    "семьдесят": 70, "восемьдесят": 80, "девяносто": 90,
    "сто": 100, "двести": 200, "триста": 300, "четыреста": 400, "пятьсот": 500,
    "шестьсот": 600, "семьсот": 700, "восемьсот": 800, "девятьсот": 900,
    "одна": 1, "две": 2,
}
SCALE_WORDS = {
    "тысяча": 10**3, "тысячи": 10**3, "тысяч": 10**3,
    "миллион": 10**6, "миллиона": 10**6, "миллионов": 10**6,
    "миллиард": 10**9, "миллиарда": 10**9, "миллиардов": 10**9,
}

# Негативный контекст: стоимость не выражена числом
AMOUNT_NEGATIVE_RE = re.compile(
    r"не указан|не установлена|определяется\s+спецификацией|не является согласованной"
)

CLAUSE_HEADER_RE = re.compile(r"^\s*(\d{1,2}(?:\.\d{1,2})?)\.\s+", re.M)


def parse_date_token(text: str) -> date | None:
    """Первая дата в тексте: числовая ДД.ММ.ГГГГ или словесная «30 апреля 2026 г.»."""
    m = DATE_NUM_RE.search(text)
    if m:
        d, mth, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            return date(y, mth, d)
        except ValueError:
            return None
    m = DATE_WORDS_RE.search(text)
    if m:
        month = MONTHS.get(m.group(2).lower())
        if month:
            try:
                return date(int(m.group(3)), month, int(m.group(1)))
            except ValueError:
                return None
    return None


def find_all_dates(text: str) -> list[tuple[date, int, int]]:
    """Все даты (числовые и словесные) со спанами."""
    out: list[tuple[date, int, int]] = []
    for m in DATE_NUM_RE.finditer(text):
        try:
            out.append((date(int(m.group(3)), int(m.group(2)), int(m.group(1))), m.start(), m.end()))
        except ValueError:
            continue
    for m in DATE_WORDS_RE.finditer(text):
        month = MONTHS.get(m.group(2).lower())
        if not month:
            continue
        try:
            out.append((date(int(m.group(3)), month, int(m.group(1))), m.start(), m.end()))
        except ValueError:
            continue
    out.sort(key=lambda t: t[1])
    return out


def words_to_number(phrase: str) -> int | None:
    """Число из слов: «один миллион двести тысяч» -> 1200000."""
    total = 0
    current = 0
    for token in re.findall(r"[а-яё]+", phrase.lower()):
        if token in UNITS:
            current += UNITS[token]
        elif token in SCALE_WORDS:
            scale = SCALE_WORDS[token]
            if current == 0:
                current = 1
            total += current * scale
            current = 0
        # прочие слова («включая», «рублей») игнорируются
    return total + current if (total or current) else None


def _line_of(text: str, pos: int) -> int:
    return text.count("\n", 0, pos)


def _evidence(file: str, text: str, start: int, end: int, clause: str | None = None) -> Evidence:
    return Evidence(
        file=file,
        quote=text[start:end],
        para=_line_of(text, start),
        clause=clause,
    )


def parse_amount_in(text: str, start: int, end: int, file: str, clause: str | None) -> FieldValue:
    """Сумма в отрезке [start, end) текста: цифры + прописью, кросс-проверка."""
    seg = text[start:end]
    if AMOUNT_NEGATIVE_RE.search(seg):
        return FieldValue(None, note="стоимость выражена без числового значения")
    digits_match = AMOUNT_DIGITS_RE.search(seg)
    words_match = AMOUNT_WORDS_RE.search(seg)
    digits_val = int(digits_match.group(1).replace(" ", "").replace("\u00a0", "")) if digits_match else None
    words_val = words_to_number(words_match.group(1)) if words_match else None

    if digits_val is None and words_val is None:
        return FieldValue(None)

    # Границы цитаты: охватить цифры и, при наличии, пропись со словом «рублей»
    q_start = digits_match.start() if digits_match else words_match.start()
    q_end = digits_match.end() if digits_match else words_match.end()
    if digits_match and words_match and words_match.start() > digits_match.end():
        q_end = max(q_end, words_match.end())
    tail = seg[q_end:q_end + 12]
    rub = RUBLE_HINT_RE.search(tail)
    if rub:
        q_end += rub.end()

    if digits_val is not None and words_val is not None and digits_val != words_val:
        return FieldValue(
            None,
            evidence=_evidence(file, seg, q_start, q_end, clause),
            note=f"расхождение цифр ({digits_val}) и прописи ({words_val}) – требует проверки",
        )
    value = digits_val if digits_val is not None else words_val
    return FieldValue(
        value,
        evidence=_evidence(file, seg, q_start, q_end, clause),
        confidence="high" if digits_val is not None else "low",
    )


def split_clauses(text: str) -> dict[str, tuple[int, int]]:
    """Границы тел пунктов: {'2.1': (start, end)} по заголовкам «2.1. …»."""
    matches = list(CLAUSE_HEADER_RE.finditer(text))
    out: dict[str, tuple[int, int]] = {}
    for i, m in enumerate(matches):
        body_start = m.end()
        body_end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        out.setdefault(m.group(1), (body_start, body_end))
    return out


def _classify(text: str) -> tuple[str, str | None]:
    head = text[:800]
    if "ПРОЕКТ ДОПОЛНИТЕЛЬНОГО СОГЛАШЕНИЯ" in head or (
        "ДОПОЛНИТЕЛЬНОЕ СОГЛАШЕНИЕ" in head and re.search(r"Статус:.*проект", text, re.IGNORECASE)
    ):
        return KIND_DRAFT, None
    if "ДОПОЛНИТЕЛЬНОЕ СОГЛАШЕНИЕ" in head:
        return KIND_AMENDMENT, None
    if "ПИСЬМО ПОСТАВЩИКА" in head:
        return KIND_LETTER, None
    if "ДОГОВОР ПОСТАВКИ" in head:
        return KIND_CONTRACT, None
    return KIND_UNKNOWN, "заголовок документа не распознан"


def _contract_no(text: str, kind: str) -> str | None:
    head = text[:800]
    if kind == KIND_CONTRACT:
        m = CONTRACT_NO_TOKEN_RE.search(head)
        return m.group(1) if m else None
    # Для ДС/письма ищем ссылку в падежной форме («К ДОГОВОРУ STN-101», «по договору STN-102»)
    refs = list(CONTRACT_REF_RE.finditer(text))
    if refs:
        return refs[0].group(1).upper()
    m = CONTRACT_NO_TOKEN_RE.search(head)
    return m.group(1).upper() if m else None


def _party(text: str, role: str) -> str | None:
    m = re.search(rf"{role}\s*:\s*([^\n.]+)", text)
    if not m:
        return None
    value = m.group(1).strip().rstrip(",")
    return value or None


def _status(text: str) -> tuple[bool | None, date | None, date | None]:
    """(подписан?, дата подписания, дата «по состоянию на»)."""
    m = re.search(r"Статус\s*:\s*([^\n]+)", text)
    is_signed: bool | None = None
    signed_date: date | None = None
    if m:
        status = m.group(1)
        has_negation = bool(re.search(r"не\s+подписан|проект", status, re.IGNORECASE))
        has_signed = bool(re.search(r"подписан", status, re.IGNORECASE))
        is_signed = (not has_negation) if (has_signed or has_negation) else None
        signed_date = parse_date_token(status)
    as_of = re.search(r"по состоянию на\s+(\d{2}\.\d{2}\.\d{4})", text, re.IGNORECASE)
    status_as_of = parse_date_token(as_of.group(1)) if as_of else None
    return is_signed, signed_date, status_as_of


def _effective_date(text: str, signed_date: date | None, header_date: date | None) -> date | None:
    m = re.search(r"вступа(?:ет|ют)\s+в\s+силу\s+(\d{2}\.\d{2}\.\d{4})", text)
    if m:
        return parse_date_token(m.group(1))
    return signed_date or header_date


def _tier_a_contract_fields(text: str, file: str) -> tuple[FieldValue | None, FieldValue | None, FieldValue | None]:
    clauses = split_clauses(text)
    amount = deadline = penalty = None

    if "2.1" in clauses:
        s, e = clauses["2.1"]
        amount = parse_amount_in(text, s, e, file, "2.1")
    if "3.1" in clauses:
        s, e = clauses["3.1"]
        dates = find_all_dates(text[s:e])
        if dates:
            d, ds, de = dates[0]
            deadline = FieldValue(d, evidence=_evidence(file, text, s + ds, s + de, "3.1"))
    if "4.1" in clauses:
        s, e = clauses["4.1"]
        seg = text[s:e]
        if not re.search(r"не\s+установлена|не\s+установлен", seg):
            rates = re.findall(r"(\d+(?:[.,]\d+)?)\s*%", seg)
            if rates:
                # Цитата – предложение с первой ставкой
                first = re.search(r"(\d+(?:[.,]\d+)?)\s*%", seg)
                sent_start = seg.rfind(".", 0, first.start()) + 1
                sent_end = seg.find(".", first.end())
                sent_end = len(seg) if sent_end == -1 else sent_end + 1
                quote = seg[sent_start:sent_end].strip()
                value = ", ".join(r.replace(".", ",") + "%" for r in rates)
                penalty = FieldValue(
                    value,
                    evidence=Evidence(file=file, quote=quote, para=_line_of(text, s + sent_start), clause="4.1"),
                )
    return amount, deadline, penalty


def _tier_b_amount(text: str, file: str) -> FieldValue | None:
    """Семантический фолбэк: «общая стоимость … N рублей» вне пункта 2.1."""
    for m in re.finditer(r"(?:общая|итоговая)\s+стоимость[^.\n]{0,300}?(\d{1,3}(?:[\s\u00a0]\d{3})+|\d{4,})", text, re.IGNORECASE):
        seg = text[m.start():m.end()]
        if AMOUNT_NEGATIVE_RE.search(seg):
            continue
        digits = m.group(1)
        value = int(digits.replace(" ", "").replace("\u00a0", ""))
        return FieldValue(
            value,
            evidence=_evidence(file, text, m.start(1), m.end(1), None),
            confidence="low",
            note="извлечено семантическим правилом (пункт 2.1 не распознан)",
        )
    return None


def _tier_b_deadline(text: str, file: str) -> FieldValue | None:
    """Семантический фолбэк: «поставить … не позднее DATE» вне пункта 3.1."""
    m = re.search(r"постав[^\n.]{0,300}?не\s+позднее\s+((?:\d{2}\.\d{2}\.\d{4})|(\d{1,2}\s+[а-яё]+\s+\d{4}))", text, re.IGNORECASE)
    if m:
        d = parse_date_token(m.group(1))
        if d:
            return FieldValue(
                d,
                evidence=_evidence(file, text, m.start(1), m.end(1), None),
                confidence="low",
                note="извлечено семантическим правилом (пункт 3.1 не распознан)",
            )
    return None


def _amendment_changes(text: str, file: str) -> list[ProposedChange]:
    """Новые редакции пунктов в подписанном ДС («изложить в следующей редакции»)."""
    changes: list[ProposedChange] = []
    for m in re.finditer(
        r"Пункт\s+(\d(?:\.\d)?)\s+договор[ау][^«\n]{0,120}?изложить\s+в\s+следующей\s+редакции\s*:\s*«([^»]+)»",
        text, re.IGNORECASE,
    ):
        clause_no, wording = m.group(1), m.group(2)
        field = {**{"2.1": FIELD_AMOUNT, "3.1": FIELD_DEADLINE}}.get(clause_no)
        if field is None:
            continue
        abs_start = m.start(2)
        if field == FIELD_AMOUNT:
            fv = parse_amount_in(text, abs_start, abs_start + len(wording), file, clause_no)
            if fv.value is not None:
                changes.append(ProposedChange(FIELD_AMOUNT, fv.value, fv.evidence))
        else:
            dates = find_all_dates(wording)
            if dates:
                d = dates[0][0]
                first = DATE_NUM_RE.search(wording) or DATE_WORDS_RE.search(wording)
                if first:
                    changes.append(ProposedChange(
                        FIELD_DEADLINE, d,
                        _evidence(file, text, abs_start + first.start(), abs_start + first.end(), clause_no),
                    ))
    return changes


def _proposal_changes(text: str, file: str) -> list[ProposedChange]:
    """Предложения: «Предлагается изменить … на N» (проект ДС) и
    «предлагаем перенести … с X на Y» (письмо)."""
    changes: list[ProposedChange] = []
    for m in re.finditer(
        r"предлага(?:ется|ем)[^.]{0,80}?изменить\s+(?:общую\s+стоимость[^.]{0,80}?|срок\s+поставки[^.]{0,80}?)на\s+(\d{2}\.\d{2}\.\d{4}|\d{1,2}\s+[а-яё]+\s+\d{4}|\d{1,3}(?:[\s\u00a0]\d{3})+|\d{4,})",
        text, re.IGNORECASE,
    ):
        token = m.group(1).strip()
        if re.match(r"\d{2}\.\d{2}\.\d{4}$", token) or re.match(r"\d{1,2}\s+[а-яё]+\s+\d{4}$", token):
            d = parse_date_token(token)
            if d:
                changes.append(ProposedChange(FIELD_DEADLINE, d, _evidence(file, text, m.start(1), m.end(1), None)))
        else:
            value = int(token.replace(" ", "").replace("\u00a0", ""))
            changes.append(ProposedChange(FIELD_AMOUNT, value, _evidence(file, text, m.start(1), m.end(1), None)))
    for m in re.finditer(
        r"перенести\s+срок\s+поставки[^.]{0,80}?с\s+(\d{2}\.\d{2}\.\d{4})\s+на\s+(\d{2}\.\d{2}\.\d{4})",
        text, re.IGNORECASE,
    ):
        d = parse_date_token(m.group(2))
        if d:
            changes.append(ProposedChange(FIELD_DEADLINE, d, _evidence(file, text, m.start(2), m.end(2), None)))
    return changes


def parse_document(filename: str, text: str) -> Doc:
    """Полный разбор одного документа."""
    kind, reason = _classify(text)
    is_signed, signed_date, status_as_of = _status(text)
    header_match = re.search(r"Дата(?:\s+проекта)?\s*:\s*(\d{2}\.\d{2}\.\d{4})", text)
    header_date = parse_date_token(header_match.group(1)) if header_match else None

    doc = Doc(
        kind=kind,
        file=filename,
        contract_no=_contract_no(text, kind),
        supplier=_party(text, "Поставщик") or _party(text, "От"),
        buyer=_party(text, "Покупатель") or _party(text, "Кому"),
        header_date=header_date,
        signed_date=signed_date,
        is_signed=is_signed,
        effective_date=None,
        status_as_of=status_as_of,
        amount=None,
        deadline=None,
        penalty=None,
        reason=reason,
    )

    if kind == KIND_CONTRACT:
        amount, deadline, penalty = _tier_a_contract_fields(text, filename)
        # Ярус Б включается только когда значения нет И нет предупреждения яруса А
        if amount is None or (amount.value is None and amount.note is None):
            fallback = _tier_b_amount(text, filename)
            if fallback is not None:
                amount = fallback
        if deadline is None or (deadline.value is None and deadline.note is None):
            fallback = _tier_b_deadline(text, filename)
            if fallback is not None:
                deadline = fallback
        doc.amount = amount if amount is not None else FieldValue(None)
        doc.deadline = deadline if deadline is not None else FieldValue(None)
        doc.penalty = penalty if penalty is not None else FieldValue(None)
    elif kind == KIND_AMENDMENT:
        doc.effective_date = _effective_date(text, signed_date, header_date)
        doc.changes = _amendment_changes(text, filename)
    elif kind in (KIND_DRAFT, KIND_LETTER):
        doc.changes = _proposal_changes(text, filename)
        if kind == KIND_DRAFT:
            doc.effective_date = _effective_date(text, signed_date, header_date)

    return doc
