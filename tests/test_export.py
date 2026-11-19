"""Tests for scripts/export.py: a private, complete copy of the data, and safe failures."""

import importlib.util
import json
import stat
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src import auth, storage

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "export.py"
NOW = datetime(2026, 11, 20, 12, 30, 5, tzinfo=timezone.utc)


def load_export():
    """Import scripts/export.py as a module."""
    spec = importlib.util.spec_from_file_location("export", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(autouse=True)
def fast_hashing(monkeypatch):
    """Use few hash iterations so the tests run fast (real code uses 600,000)."""
    monkeypatch.setattr(auth, "HASH_ITERATIONS", 1_000)
    monkeypatch.setattr(auth, "_dummy_hash", None)


@pytest.fixture
def source(tmp_path):
    """Return a temporary data file with one user in one group."""
    path = tmp_path / "data.json"
    anna = auth.sign_up("anna", "correct horse", path).id
    auth.create_group(anna, "Friends", path=path)
    return path


def test_export_is_an_exact_private_copy(source, tmp_path):
    export = load_export()
    output = tmp_path / "out" / "copy.json"
    counts = export.export(source, output)
    assert json.loads(output.read_text()) == storage.load_data(source)
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    assert counts["users"] == counts["memberships"] == counts["groups"] == 1
    assert set(counts) == set(storage.SECTIONS)


def test_exported_copy_can_be_loaded_and_restored(source, tmp_path):
    export = load_export()
    output = tmp_path / "copy.json"
    export.export(source, output)
    restored = tmp_path / "restored.json"
    storage.save_data(storage.load_data(output), restored)
    assert storage.load_data(restored) == storage.load_data(source)


def test_existing_file_is_never_overwritten(source, tmp_path):
    export = load_export()
    output = tmp_path / "copy.json"
    output.write_text("keep me")
    with pytest.raises(FileExistsError):
        export.export(source, output)
    assert output.read_text() == "keep me"


def test_damaged_data_is_reported_and_nothing_written(tmp_path):
    export = load_export()
    broken = tmp_path / "data.json"
    broken.write_text("{not json")
    output = tmp_path / "copy.json"
    with pytest.raises(storage.StorageError):
        export.export(broken, output)
    assert not output.exists()


def test_default_output_is_timestamped_in_utc(tmp_path):
    export = load_export()
    assert export.default_output(NOW, tmp_path) == tmp_path / "export-20261120-123005.json"


def test_default_directory_is_git_ignored():
    export = load_export()
    root = SCRIPT.parent.parent
    assert export.EXPORT_DIR.relative_to(root).parts[0] == "data"
    assert "data/" in (root / ".gitignore").read_text().splitlines()


def test_default_source_is_the_local_file(monkeypatch, tmp_path):
    export = load_export()
    monkeypatch.setenv("DATA_FILE", str(tmp_path / "data.json"))
    monkeypatch.setenv("DATABASE_URL", "postgresql://example.invalid/db")
    assert export._source(use_database=False) == tmp_path / "data.json"


def test_database_source_reads_the_secrets_file(monkeypatch, tmp_path):
    export = load_export()
    monkeypatch.delenv("DATA_FILE", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    secrets = tmp_path / "secrets.toml"
    secrets.write_text('DATABASE_URL = "postgresql://example.invalid/db"\n')
    assert export._source(True, secrets) is None
    assert export.os.environ["DATABASE_URL"] == "postgresql://example.invalid/db"


def test_database_source_needs_a_url(monkeypatch, tmp_path):
    export = load_export()
    monkeypatch.delenv("DATA_FILE", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(SystemExit, match="needs DATABASE_URL"):
        export._source(True, tmp_path / "missing.toml")


def test_database_source_refuses_data_file(monkeypatch, tmp_path):
    export = load_export()
    monkeypatch.setenv("DATA_FILE", str(tmp_path / "data.json"))
    with pytest.raises(SystemExit, match="DATA_FILE"):
        export._source(True, tmp_path / "missing.toml")
