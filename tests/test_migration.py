"""Tests for src/migration.py: old data (versions 1 and 2) is upgraded on loading."""

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
         "role": "admin", "balance": 99000, "restricted": False, "received": 100000},
        {"id": membership_id("u2", "g1"), "user_id": "u2", "group_id": "g1",
         "role": "member", "balance": 101000, "restricted": False, "received": 100000},
    ]  # received = the start balance everyone joined with (version 2 -> 3)


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


def test_loading_a_version_1_file_gives_the_current_version(path):
    data = storage.load_data(path)
    assert data == upgrade(version_1_data())
    assert json.loads(path.read_text(encoding="utf-8"))["schema_version"] == 1  # not rewritten


def test_first_save_writes_the_current_version(path):
    with storage.transaction(path):
        pass
    assert json.loads(path.read_text(encoding="utf-8"))["schema_version"] == storage.SCHEMA_VERSION


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


# --- version 2 -> 3: members start at 0, "received" per membership ---------------

def version_2_data() -> dict:
    """Return valid version 2 data: a start balance on the group, one top-up of 50."""
    data = upgrade(version_1_data())
    data["schema_version"] = 2
    data["groups"][0]["start_balance"] = 100000
    for membership in data["memberships"]:
        del membership["received"]
    data["topups"] = [{"id": "t1", "group_id": "g1", "amount": 5000, "created_by": "u1",
                       "created_at": "2026-11-19T12:00:00+00:00"}]
    return data


def test_version_2_gets_received_and_loses_start_balance():
    data = upgrade(version_2_data())
    assert data["schema_version"] == 3
    assert "start_balance" not in data["groups"][0]
    assert [m["received"] for m in data["memberships"]] == [105000, 105000]
    storage.validate(data)


def test_version_2_upgrade_keeps_balances_and_results():
    data = upgrade(version_2_data())
    results = {m["user_id"]: m["balance"] - m["received"] for m in data["memberships"]}
    assert results == {"u1": -6000, "u2": -4000}  # balances unchanged, read against received


def test_version_2_upgrade_does_not_change_the_original():
    original = version_2_data()
    before = copy.deepcopy(original)
    upgrade(original)
    assert original == before


def test_top_ups_of_other_groups_do_not_count():
    data = version_2_data()
    data["groups"].append({"id": "g2", "name": "Other", "admin_id": "u3",
                           "invite_code": "XYZ234", "start_balance": 500})
    data["topups"].append({"id": "t2", "group_id": "g2", "amount": 700, "created_by": "u3",
                           "created_at": "2026-11-19T12:00:00+00:00"})
    assert [m["received"] for m in upgrade(data)["memberships"]] == [105000, 105000]


@pytest.mark.parametrize("break_it", ["no topups", "membership in unknown group"])
def test_malformed_version_2_is_returned_unchanged(break_it):
    data = version_2_data()
    if break_it == "no topups":
        del data["topups"]
    else:
        data["memberships"][0]["group_id"] = "ghost"
    assert upgrade(data) == data


def test_boolean_version_is_not_mistaken_for_version_1():
    data = {"schema_version": True, "users": []}
    assert upgrade(data) is data


def test_loading_a_version_2_file_gives_the_current_version(tmp_path):
    file = tmp_path / "data.json"
    file.write_text(json.dumps(version_2_data()), encoding="utf-8")
    assert storage.load_data(file)["schema_version"] == storage.SCHEMA_VERSION
    assert service.get_user("u2", file).received == Decimal("1050.00")


def test_old_seed_start_top_up_is_not_counted_twice():
    """The version 2 seed stored the start balance as a top-up without crediting it."""
    data = version_2_data()
    data["topups"].append({"id": "seed-topup", "group_id": "g1", "amount": 100000,
                           "created_by": "u1", "created_at": "2026-11-16T12:00:00+00:00"})
    assert [m["received"] for m in upgrade(data)["memberships"]] == [105000, 105000]
