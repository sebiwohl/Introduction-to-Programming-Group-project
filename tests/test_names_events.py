"""Tests for names events: bets on one of several names (people, for example).

Covers the maths in src/betting.py, saving and checking in src/storage.py and
src/convert.py, the service functions, the stats and the HTML escaping.
"""

import copy
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from src import betting, service, stats, storage
from src.betting import BettingError
from src.convert import (
    event_from_dict, event_to_dict, group_to_dict, membership_dicts, user_to_dict,
)
from src.models import TYPE_NAMES, TYPE_OVER_UNDER, Bet, Event, Group, Option, User
from src.storage import StorageError
from src.ui import style

UTC = timezone.utc
NOW = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
CLOSE = datetime(2026, 11, 20, 19, 0, tzinfo=UTC)
AFTER = CLOSE + timedelta(hours=1)
NAMES = ["Tiago", "Marta", "João", "Pedro"]


def names_event(multi=False, status="open"):
    """Return a names event with four options: o1 Tiago, o2 Marta, o3 João, o4 Pedro."""
    return Event(id="e1", group_id="g1", creator_id="u1", title="Who shows up late?",
                 close_time=CLOSE, status=status, event_type=TYPE_NAMES,
                 options=betting.make_options(NAMES), multi_winner=multi)


def people(*balances):
    """Return users u1, u2, ... in group g1 with the given balances."""
    return {f"u{i}": User(id=f"u{i}", username=f"user{i}", password_hash="h", group_id="g1",
                          balance=Decimal(balance))
            for i, balance in enumerate(balances, start=1)}


def bet(bet_id, user_id, side, stake):
    """Return a bet on event e1."""
    return Bet(id=bet_id, event_id="e1", user_id=user_id, side=side, stake=Decimal(stake))


# --- options ------------------------------------------------------------------

def test_options_are_trimmed_and_keep_their_order():
    options = betting.make_options(["  Tiago ", "Marta"])
    assert [(o.id, o.name) for o in options] == [("o1", "Tiago"), ("o2", "Marta")]


def test_ten_options_of_forty_characters_are_allowed():
    names = [f"{i}{'x' * 39}" for i in range(10)]
    assert len(betting.make_options(names)) == 10


@pytest.mark.parametrize("names", [
    ["Tiago"],                                   # fewer than 2
    [],                                          # none
    [f"Name {i}" for i in range(11)],            # more than 10
    ["Tiago", "tiago"],                          # duplicate, ignoring case
    ["Tiago", " TIAGO "],                        # duplicate after trimming
    ["Tiago", ""],                               # empty name
    ["Tiago", "   "],                            # only spaces
    ["Tiago", "x" * 41],                         # too long
    ["Tiago", "Marta", "tiago"],                 # duplicate further down the list
    "Tiago, Marta",                              # not a list
    ["Tiago", 7],                                # not text
])
def test_bad_options_are_rejected(names):
    with pytest.raises(BettingError):
        betting.make_options(names)


# --- odds and pool share --------------------------------------------------------

def test_odds_and_pool_share_per_option():
    event = names_event()
    bets = [bet("b1", "u1", "o1", 30), bet("b2", "u2", "o2", 10)]
    assert betting.stake_per_option(bets, event) == {
        "o1": Decimal("30"), "o2": Decimal("10"), "o3": Decimal("0"), "o4": Decimal("0")}
    assert betting.option_odds(bets, event, "o1") == pytest.approx(40 / 30)
    assert betting.option_odds(bets, event, "o2") == 4.0
    assert betting.option_odds(bets, event, "o3") is None  # nobody backed it: no division by zero
    assert betting.pool_share(bets, event, "o1") == 0.75
    assert betting.pool_share(bets, event, "o3") == 0.0


def test_pool_share_is_none_without_bets():
    event = names_event()
    assert betting.pool_share([], event, "o1") is None
    assert betting.option_odds([], event, "o1") is None


def test_odds_of_unknown_option_are_rejected():
    with pytest.raises(BettingError):
        betting.option_odds([], names_event(), "o9")


# --- placing bets -------------------------------------------------------------

def test_bet_on_an_option_deducts_the_stake():
    users = people(100)
    placed = betting.place_bet(users["u1"], names_event(), "o2", Decimal("12.50"), NOW)
    assert placed.side == "o2"
    assert users["u1"].balance == Decimal("87.50")


@pytest.mark.parametrize("side", ["o9", "yes", "no", "Marta", ""])
def test_bet_on_an_option_the_event_does_not_have_is_rejected(side):
    users = people(100)
    with pytest.raises(BettingError):
        betting.place_bet(users["u1"], names_event(), side, Decimal("10"), NOW)
    assert users["u1"].balance == Decimal("100")


def test_yes_no_event_still_rejects_option_ids():
    event = Event(id="e2", group_id="g1", creator_id="u1", title="Rain", close_time=CLOSE)
    with pytest.raises(BettingError, match="Side must be one of"):
        betting.place_bet(people(100)["u1"], event, "o1", Decimal("10"), NOW)


@pytest.mark.parametrize("stake", [Decimal("0"), Decimal("-5"), Decimal("1.234"), 2.5, Decimal("101")])
def test_names_bets_follow_the_usual_stake_rules(stake):
    with pytest.raises(BettingError):
        betting.place_bet(people(100)["u1"], names_event(), "o1", stake, NOW)


def test_names_bets_follow_the_usual_event_rules():
    users = people(100)
    with pytest.raises(BettingError):
        betting.place_bet(users["u1"], names_event(status="closed"), "o1", Decimal("10"), NOW)
    with pytest.raises(BettingError):
        betting.place_bet(users["u1"], names_event(), "o1", Decimal("10"), CLOSE)
    stranger = User(id="u9", username="x", password_hash="h", group_id="g2", balance=Decimal("100"))
    with pytest.raises(BettingError):
        betting.place_bet(stranger, names_event(), "o1", Decimal("10"), NOW)


# --- payouts ------------------------------------------------------------------

def resolve(event, bets, winners, users):
    """Resolve a names event after its close time and return the total paid."""
    return betting.resolve_names_event(event, bets, winners, users, AFTER)


def test_one_winner_splits_the_whole_pool():
    users = people(0, 0, 0)
    bets = [bet("b1", "u1", "o1", 30), bet("b2", "u2", "o2", 10), bet("b3", "u3", "o2", 10)]
    event = names_event()
    assert resolve(event, bets, ["o2"], users) == Decimal("50")
    assert [b.payout for b in bets] == [Decimal("0.00"), Decimal("25.00"), Decimal("25.00")]
    assert users["u2"].balance == Decimal("25")
    assert event.status == "resolved" and event.winners == ["o2"] and event.outcome is None


def test_several_winners_share_the_pool_by_stake():
    users = people(0, 0, 0)
    bets = [bet("b1", "u1", "o1", 20), bet("b2", "u2", "o2", 10), bet("b3", "u3", "o3", 30)]
    event = names_event(multi=True)
    assert resolve(event, bets, ["o2", "o1"], users) == Decimal("60")
    # payout = stake x pool / stake on the winning options = stake x 60 / 30
    assert [b.payout for b in bets] == [Decimal("40.00"), Decimal("20.00"), Decimal("0.00")]
    assert event.winners == ["o1", "o2"]  # kept in the listed order


def test_payouts_add_up_to_the_pool_and_leftover_cents_go_to_the_biggest_stake():
    users = people(0, 0, 0, 0)
    bets = [bet("b1", "u1", "o1", 1), bet("b2", "u2", "o1", 2), bet("b3", "u3", "o1", 1),
            bet("b4", "u4", "o2", Decimal("6.01"))]
    resolve(names_event(), bets, ["o1"], users)
    # pool 10.01 split 1:2:1 -> 2.5025, 5.005, 2.5025: rounded down 2.50, 5.00, 2.50;
    # the leftover cent goes to the biggest stake (b2)
    assert [b.payout for b in bets] == [Decimal("2.50"), Decimal("5.01"), Decimal("2.50"),
                                        Decimal("0.00")]
    assert sum(b.payout for b in bets) == Decimal("10.01")


def test_leftover_cents_go_to_the_first_bet_on_a_tie():
    users = people(0, 0, 0, 0)
    bets = [bet("b1", "u1", "o1", 1), bet("b2", "u2", "o1", 1), bet("b3", "u3", "o1", 1),
            bet("b4", "u4", "o2", 7)]
    resolve(names_event(), bets, ["o1"], users)
    assert [b.payout for b in bets[:3]] == [Decimal("3.34"), Decimal("3.33"), Decimal("3.33")]
    assert sum(b.payout for b in bets) == Decimal("10")


def test_several_winners_rounding_matches_the_yes_no_rule():
    users = people(0, 0, 0)
    bets = [bet("b1", "u1", "o1", 1), bet("b2", "u2", "o2", 2), bet("b3", "u3", "o3", Decimal("0.01"))]
    resolve(names_event(multi=True), bets, ["o1", "o2"], users)
    assert [b.payout for b in bets] == betting.split_pool(Decimal("3.01"), [1, 2]) + [Decimal("0.00")]
    assert sum(b.payout for b in bets) == Decimal("3.01")


def test_nobody_on_a_winning_option_refunds_everyone():
    users = people(0, 0)
    bets = [bet("b1", "u1", "o1", 30), bet("b2", "u2", "o2", Decimal("10.50"))]
    assert resolve(names_event(), bets, ["o3"], users) == Decimal("40.50")
    assert [b.payout for b in bets] == [Decimal("30.00"), Decimal("10.50")]


def test_event_without_bets_resolves_and_pays_nothing():
    event = names_event()
    assert resolve(event, [], ["o1"], {}) == Decimal("0")
    assert event.status == "resolved"


def test_same_user_on_two_options_and_twice_on_one():
    users = people(100, 100)
    event = names_event(multi=True)
    bets = [betting.place_bet(users["u1"], event, "o1", Decimal("10"), NOW),
            betting.place_bet(users["u1"], event, "o1", Decimal("5"), NOW),
            betting.place_bet(users["u1"], event, "o2", Decimal("5"), NOW),
            betting.place_bet(users["u2"], event, "o3", Decimal("20"), NOW)]
    assert betting.stake_per_option(bets, event)["o1"] == Decimal("15")
    resolve(event, bets, ["o1"], users)
    assert users["u1"].balance == Decimal("80") + Decimal("40")


@pytest.mark.parametrize("winners", [[], ["o9"], ["o1", "o9"], "o1", None, ["o1", "o2"]])
def test_bad_winners_are_rejected_and_nothing_changes(winners):
    users = people(0, 0)
    bets = [bet("b1", "u1", "o1", 10), bet("b2", "u2", "o2", 10)]
    event = names_event()  # one winner, so ["o1", "o2"] is rejected too
    with pytest.raises(BettingError):
        resolve(event, bets, winners, users)
    assert event.status == "open" and event.winners == []
    assert all(b.payout is None for b in bets) and users["u1"].balance == 0


def test_names_event_cannot_be_resolved_before_close_or_twice():
    event = names_event()
    with pytest.raises(BettingError):
        betting.resolve_names_event(event, [], ["o1"], {}, NOW)
    resolve(event, [], ["o1"], {})
    with pytest.raises(BettingError, match="already resolved"):
        resolve(event, [], ["o2"], {})


def test_each_kind_needs_its_own_resolve_function():
    with pytest.raises(BettingError):
        betting.resolve_event(names_event(), [], "yes", {}, AFTER)
    yes_no = Event(id="e2", group_id="g1", creator_id="u1", title="Rain", close_time=CLOSE)
    with pytest.raises(BettingError):
        betting.resolve_names_event(yes_no, [], ["o1"], {}, AFTER)


def test_paid_odds():
    bets = [bet("b1", "u1", "o1", 20), bet("b2", "u2", "o2", 10), bet("b3", "u3", "o3", 30)]
    assert betting.paid_odds(bets, ["o1", "o2"]) == 2.0
    assert betting.paid_odds(bets, ["o4"]) is None


# --- storage and conversion -------------------------------------------------------

def stored_names_event(**changes):
    """Return a stored names event dict (open, one winner) with some fields changed."""
    event = {"id": "e9", "group_id": "g1", "creator_id": "u1", "title": "Who is late?",
             "description": "", "close_time": "2026-11-20T19:00:00+00:00", "status": "open",
             "outcome": None, "flags": [], "type": "names", "line": None,
             "event_time": "2026-11-20T19:00:00+00:00", "result_value": None,
             "options": [{"id": "o1", "name": "Tiago"}, {"id": "o2", "name": "Marta"}],
             "multi_winner": False, "winners": []}
    event.update(changes)
    return event


def stored_data(event, bets=()):
    """Return valid stored data with two users, a group, the event and the bets."""
    data = storage.empty_data()
    data["users"] = [{"id": "u1", "username": "anna", "password_hash": "h"},
                     {"id": "u2", "username": "ben", "password_hash": "h"}]
    data["memberships"] = [
        {"id": "m1", "user_id": "u1", "group_id": "g1", "role": "admin", "balance": 1000,
         "restricted": False, "received": 1000},
        {"id": "m2", "user_id": "u2", "group_id": "g1", "role": "member", "balance": 1000,
         "restricted": False, "received": 1000},
    ]
    data["groups"] = [{"id": "g1", "name": "Friends", "admin_id": "u1", "invite_code": "ABC"}]
    data["events"] = [event]
    data["bets"] = list(bets)
    return data


def stored_bet(bet_id, user_id, side, stake, payout=None):
    """Return a stored bet on event e9."""
    return {"id": bet_id, "event_id": "e9", "user_id": user_id, "side": side, "stake": stake,
            "payout": payout}


def test_valid_names_event_passes_validation():
    storage.validate(stored_data(stored_names_event(), [stored_bet("b1", "u1", "o2", 500)]))
    resolved = stored_names_event(status="resolved", multi_winner=True, winners=["o1", "o2"])
    storage.validate(stored_data(resolved, [stored_bet("b1", "u1", "o1", 500, 500)]))


def test_names_event_round_trips_through_the_file(tmp_path):
    path = tmp_path / "data.json"
    data = stored_data(stored_names_event(), [stored_bet("b1", "u2", "o1", 250)])
    storage.save_data(data, path)
    assert storage.load_data(path) == data
    event = event_from_dict(storage.load_data(path)["events"][0])
    assert event.event_type == TYPE_NAMES and event.options[1] == Option("o2", "Marta")
    assert event_to_dict(event) == data["events"][0]


def test_yes_no_event_is_stored_exactly_as_before():
    event = Event(id="e1", group_id="g1", creator_id="u1", title="Rain", close_time=CLOSE)
    assert set(event_to_dict(event)) == {"id", "group_id", "creator_id", "title", "description",
                                         "close_time", "status", "outcome", "flags", "type",
                                         "line", "event_time", "result_value"}


def test_stored_yes_no_event_without_names_fields_loads_as_yes_no():
    stored = {"id": "e1", "group_id": "g1", "creator_id": "u1", "title": "Rain", "description": "",
              "close_time": "2026-11-20T19:00:00+00:00", "status": "resolved", "outcome": "no",
              "flags": [], "type": "yes_no", "line": None,
              "event_time": "2026-11-20T19:00:00+00:00", "result_value": None}
    event = event_from_dict(stored)
    assert event.event_type == "yes_no" and event.options == [] and event.winners == []
    assert event_to_dict(event) == stored


def test_old_schema_1_yes_no_event_still_loads_and_resolves_as_before(tmp_path):
    """Data saved before memberships and names events existed still works the same."""
    path = tmp_path / "data.json"
    old = {"schema_version": 1, "polls": [],
           "users": [{"id": "u1", "username": "anna", "password_hash": "h", "group_id": "g1",
                      "role": "admin", "balance": 6000},
                     {"id": "u2", "username": "ben", "password_hash": "h", "group_id": "g1",
                      "role": "member", "balance": 9000}],
           "groups": [{"id": "g1", "name": "Friends", "admin_id": "u1", "invite_code": "A",
                       "start_balance": 10000}],
           "events": [{"id": "e1", "group_id": "g1", "creator_id": "u1", "title": "Rain",
                       "description": "", "close_time": "2026-11-20T19:00:00+00:00",
                       "status": "open", "outcome": None, "flags": []}],
           "bets": [{"id": "b1", "event_id": "e1", "user_id": "u1", "side": "yes", "stake": 4000,
                     "payout": None},
                    {"id": "b2", "event_id": "e1", "user_id": "u2", "side": "no", "stake": 1000,
                     "payout": None}]}
    path.write_text(json.dumps(old))
    assert service.resolve_event("u1", "e1", "no", AFTER, path) == Decimal("50")
    assert service.get_user("u2", path).balance == Decimal("140")
    saved = storage.load_data(path)
    assert saved["schema_version"] == storage.SCHEMA_VERSION and saved["events"][0]["type"] == "yes_no"
    assert "options" not in saved["events"][0] and saved["events"][0]["outcome"] == "no"


BAD_NAMES_DATA = {
    "one option": stored_names_event(options=[{"id": "o1", "name": "Tiago"}]),
    "eleven options": stored_names_event(options=[{"id": f"o{i}", "name": f"N{i}"} for i in range(11)]),
    "duplicate names ignoring case": stored_names_event(
        options=[{"id": "o1", "name": "Tiago"}, {"id": "o2", "name": "TIAGO"}]),
    "duplicate option ids": stored_names_event(
        options=[{"id": "o1", "name": "Tiago"}, {"id": "o1", "name": "Marta"}]),
    "untrimmed name": stored_names_event(
        options=[{"id": "o1", "name": " Tiago"}, {"id": "o2", "name": "Marta"}]),
    "empty name": stored_names_event(options=[{"id": "o1", "name": ""}, {"id": "o2", "name": "M"}]),
    "name too long": stored_names_event(
        options=[{"id": "o1", "name": "x" * 41}, {"id": "o2", "name": "Marta"}]),
    "extra option field": stored_names_event(
        options=[{"id": "o1", "name": "Tiago", "x": 1}, {"id": "o2", "name": "Marta"}]),
    "unknown type": stored_names_event(type="poll"),
    "names event with a line": stored_names_event(line="2.5"),
    "names event with a result number": stored_names_event(result_value="3"),
    "multi_winner not a boolean": stored_names_event(multi_winner=1),
    "winner that is not an option": stored_names_event(status="resolved", winners=["o7"]),
    "resolved without winners": stored_names_event(status="resolved", winners=[]),
    "open with winners": stored_names_event(winners=["o1"]),
    "two winners on a one-winner event": stored_names_event(status="resolved", winners=["o1", "o2"]),
    "outcome on a names event": stored_names_event(status="resolved", winners=["o1"], outcome="yes"),
    "names event without options": {k: v for k, v in stored_names_event().items() if k != "options"},
    "names event without winners": {k: v for k, v in stored_names_event().items() if k != "winners"},
    "options on a yes/no event": stored_names_event(type="yes_no"),
    "options on an over/under event": stored_names_event(type="over_under", line="2.5"),
}


@pytest.mark.parametrize("event", BAD_NAMES_DATA.values(), ids=BAD_NAMES_DATA.keys())
def test_broken_names_event_is_rejected(event):
    with pytest.raises(StorageError):
        storage.validate(stored_data(copy.deepcopy(event)))


def test_bet_on_an_option_the_event_does_not_have_is_rejected_by_storage():
    data = stored_data(stored_names_event(), [stored_bet("b1", "u1", "o3", 100)])
    with pytest.raises(StorageError, match="side must be an option id"):
        storage.validate(data)
    data["bets"][0]["side"] = "yes"
    with pytest.raises(StorageError):
        storage.validate(data)


def test_option_ids_on_a_yes_no_event_are_rejected_by_storage():
    yes_no = {k: v for k, v in stored_names_event(type="yes_no").items()
              if k not in ("options", "multi_winner", "winners")}
    with pytest.raises(StorageError, match="side must be yes or no"):
        storage.validate(stored_data(yes_no, [stored_bet("b1", "u1", "o1", 100)]))


def test_options_cannot_change_once_someone_has_bet():
    """There is no way to edit names; storage refuses data where a bet's option disappeared."""
    data = stored_data(stored_names_event(), [stored_bet("b1", "u1", "o2", 100)])
    data["events"][0]["options"] = [{"id": "o1", "name": "Tiago"}, {"id": "o3", "name": "Rui"}]
    with pytest.raises(StorageError):
        storage.validate(data)


def test_resolved_names_event_must_pay_out_its_pool():
    resolved = stored_names_event(status="resolved", winners=["o1"])
    bets = [stored_bet("b1", "u1", "o1", 100, 150), stored_bet("b2", "u2", "o2", 100, 0)]
    with pytest.raises(StorageError, match="add up to the pool"):
        storage.validate(stored_data(resolved, bets))


# --- service ----------------------------------------------------------------------

@pytest.fixture
def path(tmp_path):
    """Return a data file with group g1 (Anna admin u1, Ben u2, Cleo u3) and one yes/no event e0."""
    file = tmp_path / "data.json"
    users = [User(id="u1", username="Anna", password_hash="h", group_id="g1", role="admin",
                  balance=Decimal("100")),
             User(id="u2", username="Ben", password_hash="h", group_id="g1", balance=Decimal("100")),
             User(id="u3", username="Cleo", password_hash="h", group_id="g1", balance=Decimal("100"))]
    data = storage.empty_data()
    data["users"] = [user_to_dict(u) for u in users]
    data["memberships"] = membership_dicts(users)
    data["groups"] = [group_to_dict(Group(id="g1", name="Friends", admin_id="u1", invite_code="A"))]
    data["events"] = [event_to_dict(Event(id="e0", group_id="g1", creator_id="u1",
                                          title="Rain on Saturday", close_time=CLOSE))]
    storage.save_data(data, file)
    return file


def create(path, names=NAMES, multi=True, close=CLOSE):
    """Create a names event as Ben and return it."""
    return service.create_event("u2", "Who is going to show up later?", close, NOW, path=path,
                                options=names, multi_winner=multi)


def test_create_names_event_saves_options_and_mode(path):
    event = create(path, ["Anna", "Ben", " Tiago ", "Marta"])
    saved = next(e for e in storage.load_data(path)["events"] if e["id"] == event.id)
    assert saved["type"] == "names" and saved["multi_winner"] is True and saved["winners"] == []
    assert saved["line"] is None and saved["event_time"] == saved["close_time"]
    assert [o["name"] for o in saved["options"]] == ["Anna", "Ben", "Tiago", "Marta"]


@pytest.mark.parametrize("names", [["Tiago"], ["Tiago", "TIAGO"], ["Tiago", ""],
                                   [f"N{i}" for i in range(11)]])
def test_create_names_event_rejects_bad_names_and_saves_nothing(path, names):
    before = storage.load_data(path)
    with pytest.raises(BettingError):
        create(path, names)
    assert storage.load_data(path) == before


def test_too_long_name_error_says_which_name():
    with pytest.raises(BettingError, match="Maria Fernanda Albuq"):
        betting.make_options(["Tiago", "Maria Fernanda Albuquerque dos Santos e Sousa"])


def test_several_winners_without_names_is_rejected(path):
    before = storage.load_data(path)
    with pytest.raises(BettingError):
        service.create_event("u2", "Will it rain?", CLOSE, NOW, path=path, multi_winner=True)
    assert storage.load_data(path) == before


def test_create_without_options_is_still_a_yes_no_event(path):
    event = service.create_event("u2", "Will it rain?", CLOSE, NOW, path=path)
    saved = next(e for e in storage.load_data(path)["events"] if e["id"] == event.id)
    assert saved["type"] == "yes_no" and "options" not in saved and event.event_type == "yes_no"


def test_group_member_names(path):
    assert service.group_member_names("u3", path) == ["Anna", "Ben", "Cleo"]


def test_full_names_flow_with_two_winners(path):
    event = create(path)
    service.place_bet("u1", event.id, "o2", Decimal("30"), NOW, path)   # Anna on Marta
    first = service.event_details("u3", event.id, NOW, path)
    assert next(o for o in first.options if o.id == "o2").odds == 1.0
    service.place_bet("u2", event.id, "o3", Decimal("10"), NOW, path)   # Ben on João
    service.place_bet("u3", event.id, "o1", Decimal("20"), NOW, path)   # Cleo on Tiago
    moved = service.event_details("u1", event.id, NOW, path)
    marta = next(o for o in moved.options if o.id == "o2")
    assert moved.pool == Decimal("60") and marta.odds == 2.0 and marta.share == 0.5
    assert marta.my_stake == Decimal("30")
    with pytest.raises(BettingError):
        service.resolve_names_event("u3", event.id, ["o2", "o3"], AFTER, path)  # not creator/admin
    assert service.resolve_names_event("u2", event.id, ["o2", "o3"], AFTER, path) == Decimal("60")
    # winning stake 40 -> 60 / 40 = 1.5 per coin
    assert service.get_user("u1", path).balance == Decimal("70") + Decimal("45")
    assert service.get_user("u2", path).balance == Decimal("90") + Decimal("15")
    assert service.get_user("u3", path).balance == Decimal("80")
    done = service.event_details("u1", event.id, AFTER, path)
    assert done.event.winners == ["o2", "o3"] and done.paid_odds == 1.5
    assert [o.won for o in done.options] == [False, True, True, False]
    history = service.bet_history("u3", path)
    assert history[0].side_label == "Tiago" and history[0].winner_label == "Marta and João"
    assert history[0].event_type == "names"


def test_yes_no_event_still_resolves_exactly_as_before(path):
    service.place_bet("u1", "e0", "yes", Decimal("40"), NOW, path)
    service.place_bet("u2", "e0", "no", Decimal("10"), NOW, path)
    assert service.resolve_event("u1", "e0", "no", AFTER, path) == Decimal("50")
    assert service.get_user("u2", path).balance == Decimal("140")
    saved = storage.load_data(path)["events"][0]
    assert saved["outcome"] == "no" and saved["type"] == "yes_no" and "winners" not in saved


def test_over_under_events_are_not_names_events():
    event = Event(id="e3", group_id="g1", creator_id="u1", title="Goals", close_time=CLOSE,
                  event_type=TYPE_OVER_UNDER, line=Decimal("2.5"))
    assert not betting.is_names_event(event)
    users = people(100)
    assert betting.place_bet(users["u1"], event, "yes", Decimal("10"), NOW).side == "yes"
    with pytest.raises(BettingError):
        betting.place_bet(users["u1"], event, "o1", Decimal("10"), NOW)
    with pytest.raises(BettingError):
        betting.resolve_names_event(event, [], ["o1"], {}, AFTER)


def test_names_event_needs_names_resolve(path):
    event = create(path, multi=False)
    with pytest.raises(BettingError):
        service.resolve_event("u2", event.id, "yes", AFTER, path)
    with pytest.raises(BettingError):
        service.resolve_names_event("u1", "e0", ["o1"], AFTER, path)


# --- stats, leaderboard and display -----------------------------------------------------

def test_stats_and_leaderboard_with_a_mix_of_kinds(path):
    names = create(path, multi=False, close=CLOSE + timedelta(hours=1))  # after e0, for the streak
    service.place_bet("u1", names.id, "o2", Decimal("10"), NOW, path)   # Anna: Marta (wins)
    service.place_bet("u2", names.id, "o1", Decimal("30"), NOW, path)   # Ben: Tiago (loses)
    service.place_bet("u1", "e0", "yes", Decimal("20"), NOW, path)      # Anna: YES (loses)
    service.place_bet("u3", "e0", "no", Decimal("20"), NOW, path)       # Cleo: NO (wins)
    service.resolve_event("u1", "e0", "no", AFTER, path)
    service.resolve_names_event("u2", names.id, ["o2"], AFTER + timedelta(hours=1), path)
    page = service.page_data("u1", AFTER + timedelta(hours=2), path)
    member = stats.member_stats(page.events)
    assert (member["Anna"].right, member["Anna"].settled) == (1, 2)
    assert member["Anna"].best_odds == 4.0 and member["Anna"].streak == 1
    assert (member["Ben"].right, member["Ben"].settled, member["Ben"].streak) == (0, 1, -1)
    assert member["Cleo"].hit_rate == 1.0
    board = [(r.rank, r.username, r.balance) for r in page.leaderboard]
    assert board == [(1, "Cleo", Decimal("120")), (2, "Anna", Decimal("110")), (3, "Ben", Decimal("70"))]
    win = stats.latest_win(page.my_bets)
    assert win is not None and win.winner_label == "Marta"
    summary = next(s for s in page.events if s.event.id == names.id)
    assert stats.side_odds(summary, "o2") == 4.0


def summary_of(event, bets):
    """Return the feed summary of a names event built from bets (like service._summarize)."""
    total = betting.pool(bets)
    stakes = betting.stake_per_option(bets, event)
    rows = [service.OptionRow(id=o.id, name=o.name, stake=stakes[o.id],
                              odds=betting.option_odds(bets, event, o.id),
                              share=betting.pool_share(bets, event, o.id), my_stake=Decimal("0"),
                              won=o.id in event.winners) for o in event.options]
    bettors = [service.Bettor(b.user_id, b.side, b.stake) for b in bets]
    return service.EventSummary(event=event, pool=total, stake_yes=Decimal("0"), stake_no=Decimal("0"),
                                odds_yes=None, odds_no=None, my_stake=Decimal("0"), bettors=bettors,
                                options=rows, paid_odds=betting.paid_odds(bets, event.winners))


def test_everyone_on_winning_names_counts_as_refunded():
    event = names_event(multi=True, status="resolved")
    event.winners = ["o1", "o2"]
    summary = summary_of(event, [bet("b1", "u1", "o1", 10), bet("b2", "u2", "o2", 10)])
    assert stats.names_call_result("o1", summary) == stats.REFUNDED
    assert stats.member_stats([summary]) == {}


def test_several_winners_best_call_uses_the_odds_actually_paid():
    event = names_event(multi=True, status="resolved")
    event.winners = ["o1", "o2"]
    summary = summary_of(event, [bet("b1", "u1", "o1", 10), bet("b2", "u2", "o2", 30),
                                 bet("b3", "u3", "o3", 40)])
    member = stats.member_stats([summary])
    assert member["u1"].best_odds == 2.0  # pool 80 / winning stake 40, not 80 / 10
    assert stats.side_odds(summary, "o1") == 2.0
    assert stats.side_odds(summary, "o3") == 2.0  # a losing option keeps its own odds


def test_estimates_and_preview_for_names():
    summary = summary_of(names_event(), [bet("b1", "u1", "o1", 30), bet("b2", "u2", "o2", 10)])
    assert stats.estimated_profit(Decimal("10"), summary, "o2") == Decimal("30.00")
    assert stats.estimated_profit(Decimal("10"), summary, "o3") is None
    # a new 10 on Marta: 10 x (40 + 10) / (10 + 10) = 25
    assert stats.payout_preview(Decimal("10"), summary, "o2") == Decimal("25.00")
    assert stats.payout_preview(Decimal("10"), summary, "o4") == Decimal("50.00")
    assert stats.payout_preview(Decimal("0"), summary, "o2") is None


def test_nudge_names_the_leading_option():
    summary = summary_of(names_event(), [bet("b1", "u1", "o1", 30), bet("b2", "u2", "o2", 10)])
    found = stats.nudge([summary], "someone else")
    assert (found.side, found.label, found.share, found.bettors) == ("o1", "Tiago", 75, 2)


def test_option_names_are_escaped_everywhere_they_are_shown():
    nasty = "<img src=x onerror=alert(1)>"
    row = style.option_row(nasty, 3.4, 0.25, Decimal("10"), 1, Decimal("0"), up_to=True)
    assert "<img" not in row and "&lt;img src=x onerror=alert(1)&gt;" in row
    assert "<small>up to</small> 3.40×" in row
    z = "\u200b"  # zero-width space: stops bare addresses turning into links
    assert style.plain("[x](http://evil.example) **b**") == (
        rf"\[x\]\(http\:{z}//evil\.example\) \*\*b\*\*")
    assert style.plain("ana@mail.example www.x.example") == rf"ana@{z}mail\.example www{z}\.x\.example"
    assert style.plain("Marta") == "Marta"


def test_option_row_without_bets_shows_dashes():
    row = style.option_row("Marta", None, None, Decimal("0"), 0, Decimal("0"), up_to=True)
    assert "no bets yet" in row and "up to" not in row and row.count("—") == 2
