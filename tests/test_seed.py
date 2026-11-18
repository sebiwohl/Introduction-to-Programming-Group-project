"""Tests for scripts/seed.py: the demo data is valid and covers every event state."""

import importlib.util
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src import auth, storage

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "seed.py"
NOW = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)
PASSWORD = "demo-password-for-tests"


def load_seed():
    """Import scripts/seed.py as a module."""
    spec = importlib.util.spec_from_file_location("seed", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def seeded(tmp_path_factory):
    """Seed a temporary file once and return (seed module, path, data)."""
    seed = load_seed()
    path = tmp_path_factory.mktemp("seed") / "data.json"
    seed.seed(path, PASSWORD, NOW)
    return seed, path, storage.load_data(path)


def test_seed_data_is_valid_and_complete(seeded):
    seed, path, data = seeded
    storage.validate(data)
    assert len(data["groups"]) == 1
    assert len(data["users"]) == len(data["memberships"]) == 4
    assert {e["status"] for e in data["events"]} == {"open", "closed", "resolved"}
    assert {e["type"] for e in data["events"]} == {"yes_no", "over_under"}
    assert data["topups"] and data["notifications"]


def test_demo_accounts_have_one_admin_and_members(seeded):
    seed, path, data = seeded
    admin = auth.log_in(seed.ADMIN, PASSWORD, path)
    member = auth.log_in(seed.MEMBER, PASSWORD, path)
    assert (admin.role, member.role) == ("admin", "member")
    assert admin.group_id == member.group_id


def test_seeding_again_replaces_the_data(seeded, tmp_path):
    seed = seeded[0]
    path = tmp_path / "data.json"
    seed.seed(path, PASSWORD, NOW)
    seed.seed(path, PASSWORD, NOW)
    assert len(storage.load_data(path)["users"]) == 4


def test_default_target_is_the_file_even_with_database_url(monkeypatch, tmp_path):
    seed = load_seed()
    monkeypatch.setenv("DATABASE_URL", "postgresql://example.invalid/db")
    monkeypatch.setenv("DATA_FILE", str(tmp_path / "data.json"))
    assert seed._target(use_database=False) == tmp_path / "data.json"


def test_database_target_needs_database_url(monkeypatch):
    seed = load_seed()
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(SystemExit):
        seed._target(use_database=True)


def test_missing_password_stops_before_writing(monkeypatch, tmp_path):
    seed = load_seed()
    monkeypatch.delenv(seed.PASSWORD_ENV, raising=False)
    monkeypatch.setenv("DATA_FILE", str(tmp_path / "data.json"))
    monkeypatch.setattr("sys.argv", ["seed.py"])
    with pytest.raises(SystemExit):
        seed.main()
    assert not (tmp_path / "data.json").exists()


def test_database_seed_is_cancelled_without_yes(monkeypatch):
    seed = load_seed()
    monkeypatch.setattr("builtins.input", lambda prompt: "no")
    with pytest.raises(SystemExit):
        seed._confirm_database()
