"""Shared test set-up.

Makes `src` importable when running plain `pytest` from the project root, and
makes sure no test reaches the real database: DATABASE_URL is removed for every
test. Database tests set it themselves, to TEST_DATABASE_URL only.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture(autouse=True)
def _no_real_database(monkeypatch):
    """Remove DATABASE_URL so tests use temporary files, never the real database."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
