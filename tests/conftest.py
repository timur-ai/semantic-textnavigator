"""Общие помощники тестов."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

SAMPLES = Path(__file__).parent.parent / "sample_documents"


def load_sample(name: str) -> str:
    return (SAMPLES / name).read_text(encoding="utf-8")


@pytest.fixture
def check_date() -> date:
    return date(2026, 3, 10)
