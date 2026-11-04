"""Общие помощники тестов."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from stn.parser import parse_document

SAMPLES = Path(__file__).parent.parent / "sample_documents"
FIXTURES = Path(__file__).parent / "fixtures"


def load_sample(name: str) -> str:
    return (SAMPLES / name).read_text(encoding="utf-8")


def parse_sample(name: str):
    return parse_document(name, load_sample(name))


@pytest.fixture
def check_date() -> date:
    return date(2026, 3, 10)
