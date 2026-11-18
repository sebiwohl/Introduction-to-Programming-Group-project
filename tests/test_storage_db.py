"""Tests for the database mode of storage.py (PostgreSQL / Supabase).

The first tests need no database. The rest run only when TEST_DATABASE_URL points
to an empty test database (never the real one); otherwise they are skipped. Each
of those tests drops and recreates the two tables, so data never leaks between tests.
"""

import os
import threading
from datetime import datetime, timezone

import pytest

from src import auth, service, storage

TEST_URL = os.environ.get("TEST_DATABASE_URL")
UNREACHABLE_URL = "postgresql://someone:secretpw@127.0.0.1:1/nothing"


def sample_data():
    """Return a small valid data dict with one group, two users and one event."""
    data = storage.empty_data()
    data["users"] = [
        {"id": "u1", "username": "Anna", "password_hash": "h1", "group_id": "g1",
         "role": "admin", "balance": 100000},
        {"id": "u2", "username": "Ben", "password_hash": "h2", "group_id": "g1",
         "role": "member", "balance": 100000},
    ]
    data["groups"] = [
        {"id": "g1", "name": "Friends", "admin_id": "u1", "invite_code": "ABC234",
         "start_balance": 100000},
    ]
    data["events"] = [
        {"id": "e1", "group_id": "g1", "creator_id": "u1", "title": "Rain on Saturday",
         "description": "", "close_time": "2026-11-20T18:00:00+00:00",
         "status": "open", "outcome": None, "flags": []},
    ]
    return data


# --- no database needed -------------------------------------------------------

def test_no_database_url_means_file(monkeypatch):
    """Without DATABASE_URL the file is used."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert storage._database_url(None) is None


def test_empty_database_url_means_file(monkeypatch):
    """An empty DATABASE_URL counts as not set."""
    monkeypatch.setenv("DATABASE_URL", "")
    assert storage._database_url(None) is None


def test_database_url_selects_database(monkeypatch):
    """With DATABASE_URL and no path, the database is used."""
    monkeypatch.setenv("DATABASE_URL", UNREACHABLE_URL)
    assert storage._database_url(None) == UNREACHABLE_URL


def test_data_file_wins_over_database_url(monkeypatch, tmp_path):
    """With DATA_FILE set, the file is used even if DATABASE_URL is set too."""
    monkeypatch.setenv("DATABASE_URL", UNREACHABLE_URL)
    monkeypatch.setenv("DATA_FILE", str(tmp_path / "data.json"))
    assert storage._database_url(None) is None
    storage.save_data(sample_data())
    assert (tmp_path / "data.json").exists()
    assert storage.load_data() == sample_data()


def test_explicit_path_always_means_file(monkeypatch, tmp_path):
    """A path argument wins over DATABASE_URL, so file tests never touch a database."""
    monkeypatch.setenv("DATABASE_URL", UNREACHABLE_URL)
    file = tmp_path / "data.json"
    storage.save_data(sample_data(), file)
    assert storage.load_data(file) == sample_data()
    with storage.transaction(file) as data:
        data["users"][0]["balance"] = 5
    assert storage.load_data(file)["users"][0]["balance"] == 5


def test_unreachable_database_raises_storage_error(monkeypatch):
    """A database that cannot be reached gives StorageError, not a crash."""
    monkeypatch.setenv("DATABASE_URL", UNREACHABLE_URL)
    with pytest.raises(storage.StorageError) as caught:
        storage.load_data()
    assert "database error" in str(caught.value)


def test_error_message_never_shows_password(monkeypatch):
    """The password in DATABASE_URL never appears in an error message."""
    monkeypatch.setenv("DATABASE_URL", UNREACHABLE_URL)
    with pytest.raises(storage.StorageError) as caught:
        storage.load_data()
    assert "secretpw" not in str(caught.value)


def test_database_url_is_hidden_in_reprs(monkeypatch):
    """Error reports print reprs of arguments; the URL's repr hides the password."""
    monkeypatch.setenv("DATABASE_URL", UNREACHABLE_URL)
    url = storage._database_url(None)
    assert "secretpw" not in repr(url)
    assert "secretpw" not in repr([url])


def test_invalid_data_rejected_before_connecting(monkeypatch):
    """save_data validates first, so invalid data fails even without a database."""
    monkeypatch.setenv("DATABASE_URL", UNREACHABLE_URL)
    data = sample_data()
    data["users"][0]["balance"] = -1
    with pytest.raises(storage.StorageError) as caught:
        storage.save_data(data)
    assert "balance" in str(caught.value)


def test_unreachable_database_recovery_raises(monkeypatch):
    """load_with_recovery reports both failures when the database is down."""
    monkeypatch.setenv("DATABASE_URL", UNREACHABLE_URL)
    with pytest.raises(storage.StorageError) as caught:
        storage.load_with_recovery()
    assert "recovery failed" in str(caught.value)


# --- connection reuse (fake connections, no database needed) ----------------------

class FakeInfo:
    """Stands in for psycopg's connection.info."""

    def __init__(self, status):
        self.transaction_status = status


class FakeConnection:
    """A connection stand-in: open/closed, transaction state, optional dead server."""

    def __init__(self, status=0, dead=False):
        self.closed = False
        self.info = FakeInfo(status)
        self.dead = dead

    def execute(self, sql):
        """Fail like a dropped connection would, if dead."""
        if self.dead:
            raise RuntimeError("server closed the connection")

    def close(self):
        """Mark the connection closed."""
        self.closed = True


@pytest.fixture
def pool(monkeypatch):
    """Give storage a fake connect() and an empty pool; return the list of opened ones."""
    opened = []

    def fake_connect(psycopg, url):
        conn = FakeConnection()
        opened.append(conn)
        return conn

    pytest.importorskip("psycopg")
    storage.close_connections()
    monkeypatch.setattr(storage, "_connect", fake_connect)
    yield opened
    storage.close_connections()


def test_finished_connection_is_reused(pool):
    """A connection given back is handed out again instead of opening a new one."""
    first = storage._take_connection(None, "db")
    storage._give_back_connection("db", first)
    assert storage._take_connection(None, "db") is first
    assert len(pool) == 1


def test_busy_connections_are_not_shared(pool):
    """Two callers at the same time get two different connections."""
    a = storage._take_connection(None, "db")
    b = storage._take_connection(None, "db")
    assert a is not b and len(pool) == 2


def test_connection_in_transaction_is_not_kept(pool):
    """A connection still inside a transaction is closed, never reused."""
    conn = storage._take_connection(None, "db")
    conn.info.transaction_status = 2  # in transaction
    storage._give_back_connection("db", conn)
    assert conn.closed
    assert storage._take_connection(None, "db") is not conn


def test_closed_connection_is_not_reused(pool):
    """A connection that was closed meanwhile is skipped."""
    conn = storage._take_connection(None, "db")
    storage._give_back_connection("db", conn)
    conn.closed = True
    assert storage._take_connection(None, "db") is not conn


def test_old_dead_connection_is_replaced(pool, monkeypatch):
    """A connection idle for a long time is tested, and replaced if the server dropped it."""
    conn = storage._take_connection(None, "db")
    storage._give_back_connection("db", conn)
    conn.dead = True
    monkeypatch.setattr(storage, "IDLE_CHECK_SECONDS", -1)
    fresh = storage._take_connection(None, "db")
    assert fresh is not conn and conn.closed


def test_only_a_few_idle_connections_are_kept(pool, monkeypatch):
    """Beyond MAX_IDLE_CONNECTIONS, given-back connections are closed."""
    monkeypatch.setattr(storage, "MAX_IDLE_CONNECTIONS", 2)
    conns = [storage._take_connection(None, "db") for _ in range(3)]
    for conn in conns:
        storage._give_back_connection("db", conn)
    assert [c.closed for c in conns] == [False, False, True]


def test_close_connections_closes_kept_ones(pool):
    """close_connections() closes every kept connection."""
    conn = storage._take_connection(None, "db")
    storage._give_back_connection("db", conn)
    storage.close_connections()
    assert conn.closed


# --- real test database (skipped without TEST_DATABASE_URL) ----------------------

needs_db = pytest.mark.skipif(
    not TEST_URL, reason="set TEST_DATABASE_URL to an empty test database"
)


def run_sql(sql, params=None):
    """Run one SQL statement on the test database and return all rows (if any)."""
    import psycopg

    with psycopg.connect(TEST_URL, prepare_threshold=None) as conn:
        cur = conn.execute(sql, params)
        return cur.fetchall() if cur.description else []


@pytest.fixture
def db(monkeypatch):
    """Point storage at an empty test database: drop the tables before each test."""
    storage.close_connections()
    run_sql("DROP TABLE IF EXISTS app_data, app_data_backups")
    storage._READY_DATABASES.clear()
    monkeypatch.delenv("DATA_FILE", raising=False)
    monkeypatch.setenv("DATABASE_URL", TEST_URL)
    yield  # yields nothing, so failure reports cannot print the URL
    storage.close_connections()
    storage._READY_DATABASES.clear()


def write_raw(data):
    """Put data straight into the row, skipping validation (to simulate damage)."""
    from psycopg.types.json import Jsonb

    storage.load_data()  # creates the tables
    run_sql(
        "INSERT INTO app_data (id, data) VALUES (1, %s) "
        "ON CONFLICT (id) DO UPDATE SET data = EXCLUDED.data",
        (Jsonb(data),),
    )


@needs_db
def test_empty_database_loads_empty_data_and_saves_nothing(db):
    """With nothing saved, load_data returns empty data and creates no row."""
    assert storage.load_data() == storage.empty_data()
    assert run_sql("SELECT count(*) FROM app_data") == [(0,)]


@needs_db
def test_save_then_load_round_trip(db):
    """Saved data comes back exactly, with ints still ints."""
    storage.save_data(sample_data())
    loaded = storage.load_data()
    assert loaded == sample_data()
    assert type(loaded["users"][0]["balance"]) is int


@needs_db
def test_tables_have_row_level_security(db):
    """Both tables block Supabase's public API (they hold password hashes)."""
    storage.load_data()
    rows = run_sql(
        "SELECT relname, relrowsecurity FROM pg_class "
        "WHERE relname IN ('app_data', 'app_data_backups') ORDER BY relname"
    )
    assert rows == [("app_data", True), ("app_data_backups", True)]


@needs_db
def test_transaction_saves_changes(db):
    """Changes made inside a transaction are saved."""
    storage.save_data(sample_data())
    with storage.transaction() as data:
        data["users"][1]["balance"] = 4200
    assert storage.load_data()["users"][1]["balance"] == 4200


@needs_db
def test_transaction_on_empty_database(db):
    """The first transaction starts from empty data and creates the row."""
    with storage.transaction() as data:
        data.update(sample_data())
    assert storage.load_data() == sample_data()


@needs_db
def test_failed_transaction_saves_nothing(db):
    """If the block raises, nothing is saved and the error passes through."""
    storage.save_data(sample_data())
    with pytest.raises(ValueError):
        with storage.transaction() as data:
            data["users"][1]["balance"] = 1
            raise ValueError("rule broken")
    assert storage.load_data() == sample_data()
    assert storage.list_backups() == []


@needs_db
def test_invalid_change_in_transaction_saves_nothing(db):
    """A transaction that leaves invalid data raises StorageError and saves nothing."""
    storage.save_data(sample_data())
    with pytest.raises(storage.StorageError):
        with storage.transaction() as data:
            data["users"][1]["balance"] = -5
    assert storage.load_data() == sample_data()


@needs_db
def test_nested_transaction_rejected(db):
    """A nested transaction raises instead of deadlocking."""
    with pytest.raises(storage.StorageError):
        with storage.transaction():
            with storage.transaction():
                pass


@needs_db
def test_each_save_keeps_a_backup(db):
    """Every save after the first keeps the previous data as a backup."""
    storage.save_data(sample_data())
    assert storage.list_backups() == []
    with storage.transaction() as data:
        data["users"][1]["balance"] = 1
    with storage.transaction() as data:
        data["users"][1]["balance"] = 2
    names = storage.list_backups()
    assert len(names) == 2
    assert all(name.startswith("backup-") for name in names)


@needs_db
def test_backups_are_pruned(db, monkeypatch):
    """Only the newest MAX_BACKUPS backups are kept."""
    monkeypatch.setattr(storage, "MAX_BACKUPS", 3)
    for balance in range(1, 8):
        data = sample_data()
        data["users"][1]["balance"] = balance
        storage.save_data(data)
    assert len(storage.list_backups()) == 3


@needs_db
def test_damaged_data_raises_on_load(db):
    """Data that fails validation is reported, not returned."""
    broken = sample_data()
    broken["users"][0]["balance"] = 1.5
    write_raw(broken)
    with pytest.raises(storage.StorageError) as caught:
        storage.load_data()
    assert "stored data is invalid" in str(caught.value)


@needs_db
def test_recovery_restores_newest_valid_backup(db):
    """load_with_recovery restores the newest valid backup and keeps the broken copy."""
    storage.save_data(sample_data())
    newer = sample_data()
    newer["users"][1]["balance"] = 777
    storage.save_data(newer)  # backup now holds sample_data()
    storage.save_data(newer)  # backup now also holds `newer`
    broken = sample_data()
    broken["groups"] = []  # users point to a missing group
    write_raw(broken)
    data, warning = storage.load_with_recovery()
    assert data == newer
    assert "Restored from backup-" in warning
    assert "corrupt row" in warning
    corrupt = run_sql("SELECT data FROM app_data_backups WHERE kind = 'corrupt'")
    assert corrupt == [(broken,)]


@needs_db
def test_recovery_without_backups_fails(db):
    """With damaged data and no backup, recovery raises and keeps the data as it is."""
    broken = sample_data()
    broken["schema_version"] = 99
    write_raw(broken)
    with pytest.raises(storage.StorageError) as caught:
        storage.load_with_recovery()
    assert "no valid backup" in str(caught.value)
    assert run_sql("SELECT data FROM app_data") == [(broken,)]


@needs_db
def test_recovery_does_nothing_when_data_is_fine(db):
    """Good data loads without a warning and without a restore."""
    storage.save_data(sample_data())
    assert storage.load_with_recovery() == (sample_data(), None)


@needs_db
def test_restore_latest_backup_returns_name(db):
    """restore_latest_backup returns the backup's name in database mode."""
    storage.save_data(sample_data())
    storage.save_data(sample_data())
    name = storage.restore_latest_backup()
    assert name == storage.list_backups()[0]


@needs_db
def test_parallel_transactions_lose_no_update(db):
    """Ten threads adding 1 each end at +10: no save overwrites another."""
    storage.save_data(sample_data())

    def add_one():
        with storage.transaction() as data:
            data["users"][1]["balance"] += 1

    threads = [threading.Thread(target=add_one) for _ in range(10)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert storage.load_data()["users"][1]["balance"] == 100010


@needs_db
def test_app_works_end_to_end_on_database(db, monkeypatch):
    """Sign-up, group creation, an event and login all go through the database."""
    monkeypatch.setattr(auth, "HASH_ITERATIONS", 1000)
    user = auth.sign_up("Cleo", "password123")
    auth.create_group(user.id, "Flatmates")
    now = datetime(2026, 11, 16, 12, 0, tzinfo=timezone.utc)
    close = datetime(2026, 11, 20, 18, 0, tzinfo=timezone.utc)
    service.create_event(user.id, "Rain on Saturday", close, now)
    assert auth.log_in("cleo", "password123").id == user.id
    stored = storage.load_data()
    assert [u["username"] for u in stored["users"]] == ["Cleo"]
    assert stored["groups"][0]["name"] == "Flatmates"
    assert stored["events"][0]["title"] == "Rain on Saturday"


@needs_db
def test_real_connection_is_reused_across_calls(db, monkeypatch):
    """Several loads and saves in a row open only one database connection."""
    opened = []
    real_connect = storage._connect

    def counting_connect(psycopg, url):
        opened.append(url)
        return real_connect(psycopg, url)

    storage.load_data()  # creates the tables (that uses its own connection)
    storage.close_connections()
    monkeypatch.setattr(storage, "_connect", counting_connect)
    storage.save_data(sample_data())
    for _ in range(3):
        storage.load_data()
    with storage.transaction() as data:
        data["users"][1]["balance"] = 7
    assert len(opened) == 1
    assert storage.load_data()["users"][1]["balance"] == 7


@needs_db
def test_failed_transaction_leaves_connection_usable(db):
    """After a rolled-back transaction, the kept connection still works."""
    storage.save_data(sample_data())
    with pytest.raises(ValueError):
        with storage.transaction() as data:
            data["users"][1]["balance"] = 1
            raise ValueError("rule broken")
    assert storage.load_data() == sample_data()


@needs_db
def test_names_event_round_trips_through_the_database(db):
    """A names event (options, winner mode, winners) and a bet on an option come back exactly."""
    data = sample_data()
    data["events"].append(
        {"id": "e2", "group_id": "g1", "creator_id": "u2", "title": "Who is late?",
         "description": "", "close_time": "2026-11-20T18:00:00+00:00", "status": "open",
         "outcome": None, "flags": [], "kind": "names",
         "options": [{"id": "o1", "name": "Tiago"}, {"id": "o2", "name": "<b>Marta</b>"}],
         "multi_winner": True, "winners": []}
    )
    data["bets"] = [{"id": "b1", "event_id": "e2", "user_id": "u1", "side": "o2", "stake": 500,
                     "payout": None}]
    storage.save_data(data)
    assert storage.load_data() == data
