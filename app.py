"""Semantic TextNavigator: Gradio-интерфейс прототипа.

Слой интерфейса: связывает загрузку файлов, дату проверки и четыре вопроса
пользователя с детерминированным доменом (stn.parser / stn.resolver / stn.qa).
Ответы на вопросы вычисляются из загруженных документов, ничего не зашито.
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import gradio as gr

from stn.parser import parse_document
from stn.qa import delivery_before, field_answer, fmt_evidence, fmt_value
from stn.resolver import build_states, orphans, unknown_docs

SAMPLES_DIR = Path(__file__).parent / "sample_documents"

DEFAULT_CHECK_DATE = "10.03.2026"


def _parse_check_date(raw: str) -> date | str:
    try:
        return datetime.strptime((raw or "").strip(), "%d.%m.%Y").date()
    except ValueError:
        return f"Дата проверки не распознана («{raw}»), формат – ДД.ММ.ГГГГ."


def _load_files(files: list) -> list[tuple[str, str]]:
    """[(имя, текст)] из загруженных файлов."""
    out: list[tuple[str, str]] = []
    for f in files or []:
        path = Path(f.name) if hasattr(f, "name") else Path(f)
        out.append((path.name, path.read_text(encoding="utf-8", errors="replace")))
    return out


def _evidence_html(ev, title: str) -> str:
    if ev is None:
        return f"<li>{title}: основание не найдено – требует проверки</li>"
    clause = f", пункт {ev.clause}" if ev.clause else ""
    return (
        f"<li>{title}: <a href=\"#para-{ev.para}\" onclick=\"return false\">абзац {ev.para + 1}</a>"
        f"{clause}, файл «{ev.file}» – «{ev.quote}»</li>"
    )


def _contract_details_html(state) -> str:
    parts = [f"<h4>Договор {state.contract_no} – {state.supplier or 'поставщик не определён'}</h4><ul>"]
    parts.append(_evidence_html(state.amount.evidence, "Сумма: " + fmt_value(state.amount)))
    chain_a = " → ".join(e.file for e in state.changed_by.get("amount", []))
    if chain_a:
        parts.append(f"<li>Сумма изменена документом: {chain_a}</li>")
    parts.append(_evidence_html(state.deadline.evidence, "Срок поставки: " + fmt_value(state.deadline)))
    chain_d = " → ".join(e.file for e in state.changed_by.get("deadline", []))
    if chain_d:
        parts.append(f"<li>Срок изменён документом: {chain_d}</li>")
    parts.append(_evidence_html(state.penalty.evidence, "Неустойка: " + fmt_value(state.penalty)))
    if state.amount.confidence == "low":
        parts.append(f"<li>Внимание: сумма – низкая уверенность ({state.amount.note})</li>")
    if state.deadline.confidence == "low":
        parts.append(f"<li>Внимание: срок – низкая уверенность ({state.deadline.note})</li>")
    if state.amount.note and "расхождение" in (state.amount.note or ""):
        parts.append(f"<li>Внимание: {state.amount.note}</li>")
    parts.append("</ul>")
    return "".join(parts)


def _proposals_html(states) -> str:
    rows = []
    for state in states:
        for file, kind_word, supplier, change, status in state.proposals:
            what = f"{fmt_value(change).replace('не позднее ', 'перенос на ') if change else 'значение не распознано'}" if change else "значение не распознано"
            if change and change.field == "amount":
                what = f"{change.value:,} руб.".replace(",", " ")
            rows.append(
                f"<tr><td>{state.contract_no}</td><td>{kind_word} {supplier or ''}</td>"
                f"<td>{what}</td><td>{change.evidence.quote if change else '—'}</td>"
                f"<td>«{file}»</td><td>{status}</td></tr>"
            )
    if not rows:
        return "<p>Несогласованных предложений не найдено.</p>"
    return (
        "<table border='1' cellpadding='4' cellspacing='0' style='border-collapse:collapse'>"
        "<tr><th>Договор</th><th>Документ</th><th>Предлагается</th><th>Цитата</th><th>Файл</th><th>Статус</th></tr>"
        + "".join(rows) + "</table>"
    )


def _attention_html(states, unknown, orphan_docs) -> str:
    items = []
    for state in states:
        for file, note in state.later_amendments:
            items.append(f"<li>«{file}» ({state.contract_no}): {note} – значение не меняется</li>")
        for file, reason in state.requires_check:
            items.append(f"<li>«{file}» ({state.contract_no}): {reason}</li>")
    for file, reason in unknown:
        items.append(f"<li>«{file}»: {reason}</li>")
    for file, reason in orphan_docs:
        items.append(f"<li>«{file}»: {reason}</li>")
    if not items:
        return "<p>Документов, требующих проверки, нет.</p>"
    return "<ul>" + "".join(items) + "</ul>"


def _doc_viewer_html(filename: str | None, texts: dict[str, str], quotes: dict[str, list[str]]) -> str:
    if not filename or filename not in texts:
        return "<p>Выберите файл для просмотра.</p>"
    lines = texts[filename].splitlines()
    marks = quotes.get(filename, [])
    html = ["<div style='max-height:420px;overflow:auto;font-family:monospace;font-size:13px'>"]
    for i, line in enumerate(lines):
        marked = line
        for q in marks:
            if q and q in marked:
                marked = marked.replace(q, f"<mark>{q}</mark>", 1)
        html.append(f"<div id='para-{i}' style='padding:2px 0'>{marked or '&nbsp;'}</div>")
    html.append("</div>")
    return "".join(html)


def _collect_quotes(states) -> dict[str, list[str]]:
    quotes: dict[str, list[str]] = {}
    for state in states:
        items = [state.amount, state.deadline, state.penalty]
        for fv in items:
            if fv.evidence and fv.evidence.quote:
                quotes.setdefault(fv.evidence.file, []).append(fv.evidence.quote)
        for chain in state.changed_by.values():
            for ev in chain:
                if ev.quote:
                    quotes.setdefault(ev.file, []).append(ev.quote)
        for file, kind_word, supplier, change, status in state.proposals:
            if change and change.evidence.quote:
                quotes.setdefault(change.evidence.file, []).append(change.evidence.quote)
    return quotes


def analyze(files, check_date_raw: str):
    """Полный пересчёт: таблица, детали, блоки, просмотр документов."""
    parsed = _parse_check_date(check_date_raw)
    if isinstance(parsed, str):
        return parsed, "<p>—</p>", "<p>—</p>", "<p>—</p>", gr.update(choices=[], value=None), "<p>—</p>", {}, []
    check_date = parsed

    pairs = _load_files(files)
    if not pairs:
        return (
            "Загрузите TXT-файлы (договоры, допсоглашения, письма).",
            "<p>—</p>", "<p>—</p>", "<p>—</p>", gr.update(choices=[], value=None), "<p>—</p>", {}, [],
        )

    docs = [parse_document(name, text) for name, text in pairs]
    states = build_states(docs, check_date)

    table = [[
        s.contract_no,
        s.supplier or "нет данных",
        fmt_value(s.amount),
        fmt_value(s.deadline),
        "изменено: " + ", ".join(sorted({e.file for v in s.changed_by.values() for e in v})) if s.changed_by else "без изменений",
    ] for s in states]

    details = "".join(_contract_details_html(s) for s in states) or "<p>Договоры не распознаны.</p>"
    proposals = _proposals_html(states)
    attention = _attention_html(states, unknown_docs(docs), orphans(docs, states))

    texts = {name: text for name, text in pairs}
    quotes = _collect_quotes(states)
    choices = sorted(texts)
    first = choices[0] if choices else None
    viewer = _doc_viewer_html(first, texts, quotes)

    session = {"texts": texts, "quotes": quotes, "states": states, "check_date": check_date}
    return table, details, proposals, attention, gr.update(choices=choices, value=first), viewer, session, states


def pick_doc(filename: str, session: dict):
    if not session:
        return "<p>Сначала выполните проверку.</p>"
    return _doc_viewer_html(filename, session.get("texts", {}), session.get("quotes", {}))


def question(state_store, which: str):
    """Ответ на один из четырёх вопросов пользователя (вычисляется из данных)."""
    states = state_store or []
    if not states:
        return "Сначала загрузите документы и нажмите «Проверить документы»."
    if which == "q1":
        return field_answer(states, "STN-101", "deadline")
    if which == "q2":
        return field_answer(states, "STN-102", "amount")
    if which == "q3":
        return field_answer(states, "STN-103", "amount")
    return delivery_before(states, date(2026, 4, 1))


def load_examples():
    """Подкладывает шесть учебных файлов в поле загрузки."""
    files = sorted(SAMPLES_DIR.glob("*.txt"))
    return files


def build_demo() -> gr.Blocks:
    with gr.Blocks(title="Semantic TextNavigator") as demo:
        gr.Markdown(
            "## Semantic TextNavigator – сверка условий договоров поставки\n"
            "Загрузите TXT-документы (договоры, допсоглашения, письма). Прототип покажет сумму и срок "
            "поставки по каждому договору на дату проверки с проверяемым основанием: файл, дословная цитата, "
            "абзац. Изменения применяются только от подписанных и вступивших в силу допсоглашений; "
            "письма и проекты показываются отдельно как несогласованные предложения. "
            "Отсутствующие сведения отображаются как «нет данных»."
        )
        with gr.Row():
            with gr.Column(scale=1):
                files = gr.Files(label="Документы (TXT)", file_types=[".txt"])
                check_date = gr.Textbox(label="Дата проверки (ДД.ММ.ГГГГ)", value=DEFAULT_CHECK_DATE)
                with gr.Row():
                    btn = gr.Button("Проверить документы", variant="primary")
                    btn_examples = gr.Button("Загрузить примеры")
                doc_names = gr.Dropdown(label="Просмотр документа (цитаты подсвечены)", choices=[], value=None)
                viewer = gr.HTML("<p>Загрузите документы и нажмите «Проверить документы».</p>")
            with gr.Column(scale=2):
                table = gr.Dataframe(
                    headers=["Договор", "Поставщик", "Сумма", "Срок поставки", "Статус изменений"],
                    label="Договоры на дату проверки",
                    interactive=False,
                )
                details = gr.HTML("<p>—</p>")
                gr.Markdown("### Несогласованные предложения (не меняют условия)")
                proposals = gr.HTML("<p>—</p>")
                gr.Markdown("### Требует проверки")
                attention = gr.HTML("<p>—</p>")
        gr.Markdown("## Вопросы пользователя")
        session = gr.State({})
        state_store = gr.State([])
        with gr.Row():
            q1_btn = gr.Button("1. Срок поставки по STN-101 на дату проверки")
            q2_btn = gr.Button("2. Сумма договора STN-102")
            q3_btn = gr.Button("3. Сумма договора STN-103")
            q4_btn = gr.Button("4. Поставка раньше 01.04.2026 + несогласованные изменения срока")
        answers = gr.Markdown("Ответ появится после нажатия кнопки вопроса.")

        btn.click(analyze, inputs=[files, check_date],
                  outputs=[table, details, proposals, attention, doc_names, viewer, session, state_store])
        doc_names.change(pick_doc, inputs=[doc_names, session], outputs=[viewer])
        btn_examples.click(load_examples, outputs=[files])
        q1_btn.click(question, inputs=[state_store, gr.State("q1")], outputs=[answers])
        q2_btn.click(question, inputs=[state_store, gr.State("q2")], outputs=[answers])
        q3_btn.click(question, inputs=[state_store, gr.State("q3")], outputs=[answers])
        q4_btn.click(question, inputs=[state_store, gr.State("q4")], outputs=[answers])
    return demo


if __name__ == "__main__":
    build_demo().launch()
