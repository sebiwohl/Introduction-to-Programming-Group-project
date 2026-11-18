"""Tests for src/migration.py: version 1 data is upgraded to version 2 on loading."""

import copy
import json
from decimal import Decimal

import pytest

from src import auth, service, storage
from src.migration import membership_id, upgrade

ANNA_HASH = auth.hash_password("secret123")  # once, so every copy of the data is equal


def version_1_data() -> dict:
    """Return valid version 1 data: users carry group, role and balance."""
    return {
        "schema_version": 1,
        "users": [
            {"id": "u1", "username": "Anna", "password_hash": ANNA_HASH,
             "group_id": "g1", "role": "admin", "balance": 99000},
            {"id": "u2", "username": "Ben", "password_hash": "h2", "group_id": "g1",
             "role": "member", "balance": 101000},
            {"id": "u3", "username": "Cleo", "password_hash": "h3", "group_id": None,
             "role": "member", "balance": 0},
        ],
        "groups": [{"id": "g1", "name": "Friends", "admin_id": "u1",
                    "invite_code": "ABC234", "start_balance": 100000}],
        "events": [{"id": "e1", "group_id": "g1", "creator_id": "u1", "title": "Rain",
                    "description": "", "close_time": "2026-11-20T18:00:00+00:00",
                    "status": "open", "outcome": None, "flags": []}],
        "bets": [{"id": "b1", "event_id": "e1", "user_id": "u1", "side": "yes",
                  "stake": 1000, "payout": None}],
        "polls": [],
    }


@pytest.fixture
def path(tmp_path):
    """Return a data file that holds version 1 data."""
    file = tmp_path / "data.json"
    file.write_text(json.dumps(version_1_data()), encoding="utf-8")
    return file


def test_upgrade_moves_group_role_and_balance_into_memberships():
    data = upgrade(version_1_data())
    assert data["users"][0] == {"id": "u1", "username": "Anna",
                                "password_hash": data["users"][0]["password_hash"]}
    assert data["memberships"] == [
        {"id": membership_id("u1", "g1"), "user_id": "u1", "group_id": "g1",
         "role": "admin", "balance": 99000, "restricted": False},
        {"id": membership_id("u2", "g1"), "user_id": "u2", "group_id": "g1",
         "role": "member", "balance": 101000, "restricted": False},
    ]


def test_user_without_group_gets_no_membership():
    data = upgrade(version_1_data())
    assert all(m["user_id"] != "u3" for m in data["memberships"])


def test_upgrade_adds_event_fields_and_empty_lists():
    data = upgrade(version_1_data())
    event = data["events"][0]
    assert (event["type"], event["line"], event["result_value"]) == ("yes_no", None, None)
    assert event["event_time"] == event["close_time"]
    assert (data["topups"], data["repayments"], data["notifications"]) == ([], [], [])
    assert data["schema_version"] == storage.SCHEMA_VERSION
    storage.validate(data)


def test_upgrade_does_not_change_the_original():
    original = version_1_data()
    before = copy.deepcopy(original)
    upgrade(original)
    assert original == before


@pytest.mark.parametrize("data", [None, [], "text", {"schema_version": 2}, {"schema_version": 7}])
def test_other_data_is_returned_unchanged(data):
    assert upgrade(data) is data


def test_malformed_version_1_data_is_returned_unchanged():
    broken = version_1_data()
    del broken["users"][0]["group_id"]
    assert upgrade(broken) is broken


def test_malformed_version_1_file_is_reported_not_crashing(tmp_path):
    broken = version_1_data()
    broken["users"][0]["balance"] = -1
    file = tmp_path / "data.json"
    file.write_text(json.dumps(broken), encoding="utf-8")
    with pytest.raises(storage.StorageError):
        storage.load_data(file)


def test_loading_a_version_1_file_gives_version_2(path):
    data = storage.load_data(path)
    assert data == upgrade(version_1_data())
    assert json.loads(path.read_text(encoding="utf-8"))["schema_version"] == 1  # not rewritten


def test_first_save_writes_version_2(path):
    with storage.transaction(path):
        pass
    assert json.loads(path.read_text(encoding="utf-8"))["schema_version"] == 2


def test_app_reads_balances_from_upgraded_data(path):
    assert service.get_user("u2", path).balance == Decimal("1010.00")
    user = auth.log_in("Anna", "secret123", path)
    assert (user.group_id, user.role, user.balance) == ("g1", "admin", Decimal("990.00"))


def test_betting_on_upgraded_data_updates_the_membership(path):
    from datetime import datetime, timezone

    now = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)
    service.place_bet("u2", "e1", "no", Decimal("10"), now, path)
    data = storage.load_data(path)
    ben = next(m for m in data["memberships"] if m["user_id"] == "u2")
    assert ben["balance"] == 100000


def test_version_1_backup_can_be_restored(path):
    storage.save_data(storage.load_data(path), path)  # the version 1 file becomes a backup
    path.write_text("{ broken", encoding="utf-8")
    data, warning = storage.load_with_recovery(path)
    assert warning is not None
    assert data == upgrade(version_1_data())
