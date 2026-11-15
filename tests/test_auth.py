"""Tests for src/auth.py: passwords, accounts, groups and permissions."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from src import auth, service, storage
from src.auth import AuthError
from src.betting import BettingError
from src.models import Event, User

CLOSE = datetime(2026, 11, 20, 19, 0, tzinfo=timezone.utc)
PASSWORD = "correct horse"


@pytest.fixture(autouse=True)
def fast_hashing(monkeypatch):
    """Use few hash iterations so the tests run fast (real code uses 600,000)."""
    monkeypatch.setattr(auth, "HASH_ITERATIONS", 1_000)
    monkeypatch.setattr(auth, "_dummy_hash", None)


@pytest.fixture
def path(tmp_path):
    """Return the path of an empty, temporary data file."""
    return tmp_path / "data.json"


def saved_user(path, user_id):
    """Return the stored dict of one user."""
    return next(u for u in storage.load_data(path)["users"] if u["id"] == user_id)


# --- passwords ----------------------------------------------------------------

def test_hash_is_salted_and_never_contains_the_password():
    first, second = auth.hash_password(PASSWORD), auth.hash_password(PASSWORD)
    assert first != second  # different salt each time
    assert PASSWORD not in first
    assert first.startswith("pbkdf2_sha256$1000$")


def test_verify_password_accepts_right_and_rejects_wrong():
    stored = auth.hash_password(PASSWORD)
    assert auth.verify_password(PASSWORD, stored)
    assert not auth.verify_password("Correct horse", stored)
    assert not auth.verify_password("", stored)


@pytest.mark.parametrize("stored", ["", "garbage", "md5$1$aa$bb", "pbkdf2_sha256$x$zz$yy", None])
def test_verify_password_rejects_broken_hashes(stored):
    assert not auth.verify_password(PASSWORD, stored)


def test_old_hashes_still_verify_after_raising_iterations(monkeypatch):
    stored = auth.hash_password(PASSWORD)
    monkeypatch.setattr(auth, "HASH_ITERATIONS", 2_000)
    assert auth.verify_password(PASSWORD, stored)


# --- sign up --------------------------------------------------------------------

def test_sign_up_stores_only_a_hash(path):
    user = auth.sign_up("  Anna  ", PASSWORD, path)
    stored = saved_user(path, user.id)
    assert stored["username"] == "Anna"
    assert stored["password_hash"] != PASSWORD and PASSWORD not in path.read_text()
    assert (stored["group_id"], stored["role"], stored["balance"]) == (None, "member", 0)


@pytest.mark.parametrize("taken", ["Anna", "anna", " ANNA "])
def test_sign_up_rejects_taken_username_ignoring_case(path, taken):
    auth.sign_up("Anna", PASSWORD, path)
    with pytest.raises(AuthError, match="taken"):
        auth.sign_up(taken, PASSWORD, path)


@pytest.mark.parametrize("username", ["", "ab", "   ab   ", "x" * 31, None])
def test_sign_up_rejects_bad_username(path, username):
    with pytest.raises(AuthError, match="Username"):
        auth.sign_up(username, PASSWORD, path)


@pytest.mark.parametrize("password", ["", "short", "1234567", None])
def test_sign_up_rejects_short_password(path, password):
    with pytest.raises(AuthError, match="Password"):
        auth.sign_up("Anna", password, path)
    assert not path.exists()


# --- log in ---------------------------------------------------------------------

def test_log_in_with_right_password(path):
    created = auth.sign_up("Anna", PASSWORD, path)
    assert auth.log_in("Anna", PASSWORD, path) == created


def test_log_in_ignores_username_case_and_spaces(path):
    created = auth.sign_up("Anna", PASSWORD, path)
    assert auth.log_in("  anna ", PASSWORD, path).id == created.id


def test_wrong_password_and_unknown_user_give_same_message(path):
    auth.sign_up("Anna", PASSWORD, path)
    messages = []
    for username, password in [("Anna", "wrong password"), ("Nobody", PASSWORD)]:
        with pytest.raises(AuthError) as error:
            auth.log_in(username, password, path)
        messages.append(str(error.value))
    assert messages == [auth.LOGIN_FAILED, auth.LOGIN_FAILED]


def test_log_in_rejects_non_text(path):
    with pytest.raises(AuthError):
        auth.log_in(None, None, path)


# --- groups ---------------------------------------------------------------------

def test_create_group_makes_creator_admin_with_start_balance(path):
    anna = auth.sign_up("Anna", PASSWORD, path)
    group = auth.create_group(anna.id, " Friends ", Decimal("500.50"), path)
    stored = saved_user(path, anna.id)
    assert (group.name, group.admin_id, group.start_balance) == ("Friends", anna.id, Decimal("500.50"))
    assert (stored["group_id"], stored["role"], stored["balance"]) == (group.id, "admin", 50050)


def test_create_group_default_balance_is_1000(path):
    anna = auth.sign_up("Anna", PASSWORD, path)
    auth.create_group(anna.id, "Friends", path=path)
    assert service.get_user(anna.id, path).balance == 1000


def test_invite_code_uses_safe_characters(path):
    anna = auth.sign_up("Anna", PASSWORD, path)
    code = auth.create_group(anna.id, "Friends", path=path).invite_code
    assert len(code) == auth.INVITE_LENGTH
    assert set(code) <= set(auth.INVITE_ALPHABET)


@pytest.mark.parametrize("name", ["", "   ", "x" * 51, None])
def test_create_group_rejects_bad_name(path, name):
    anna = auth.sign_up("Anna", PASSWORD, path)
    with pytest.raises(AuthError, match="Group name"):
        auth.create_group(anna.id, name, path=path)


@pytest.mark.parametrize("balance", [0, -10, 1.5, Decimal("1.234"), True, "100"])
def test_create_group_rejects_bad_start_balance(path, balance):
    anna = auth.sign_up("Anna", PASSWORD, path)
    with pytest.raises(AuthError, match="Start balance"):
        auth.create_group(anna.id, "Friends", balance, path)


def test_cannot_create_second_group(path):
    anna = auth.sign_up("Anna", PASSWORD, path)
    auth.create_group(anna.id, "Friends", path=path)
    with pytest.raises(AuthError, match="already in a group"):
        auth.create_group(anna.id, "More friends", path=path)


def test_join_group_with_code_as_member(path):
    anna = auth.sign_up("Anna", PASSWORD, path)
    group = auth.create_group(anna.id, "Friends", Decimal("250"), path)
    ben = auth.sign_up("Ben", PASSWORD, path)
    joined = auth.join_group(ben.id, f"  {group.invite_code.lower()} ", path)
    stored = saved_user(path, ben.id)
    assert joined == group
    assert (stored["group_id"], stored["role"], stored["balance"]) == (group.id, "member", 25000)


@pytest.mark.parametrize("code", ["WRONG1", "", None])
def test_join_group_rejects_unknown_code(path, code):
    ben = auth.sign_up("Ben", PASSWORD, path)
    with pytest.raises(AuthError, match="invite code"):
        auth.join_group(ben.id, code, path)


def test_cannot_join_while_in_a_group(path):
    anna = auth.sign_up("Anna", PASSWORD, path)
    first = auth.create_group(anna.id, "Friends", path=path)
    dan = auth.sign_up("Dan", PASSWORD, path)
    auth.create_group(dan.id, "Others", path=path)
    with pytest.raises(AuthError, match="already in a group"):
        auth.join_group(dan.id, first.invite_code, path)


def test_unknown_user_cannot_create_or_join(path):
    with pytest.raises(AuthError, match="Unknown user"):
        auth.create_group("ghost", "Friends", path=path)
    with pytest.raises(AuthError, match="Unknown user"):
        auth.join_group("ghost", "ABCDEF", path)


# --- permissions ----------------------------------------------------------------

def make_user(user_id, role="member", group_id="g1"):
    """Return a user for permission checks (no storage needed)."""
    return User(id=user_id, username=user_id, password_hash="h", group_id=group_id, role=role)


def make_event(creator_id="u2", group_id="g1"):
    """Return an event for permission checks."""
    return Event(id="e1", group_id=group_id, creator_id=creator_id,
                 title="Test", close_time=CLOSE)


ADMIN, CREATOR, MEMBER = make_user("u1", "admin"), make_user("u2"), make_user("u3")


@pytest.mark.parametrize("action", sorted(auth.MEMBER_ACTIONS))
@pytest.mark.parametrize("user", [ADMIN, CREATOR, MEMBER])
def test_everyone_in_group_may_do_member_actions(user, action):
    auth.require_permission(user, action, make_event())


@pytest.mark.parametrize("user", [ADMIN, CREATOR])
def test_creator_and_admin_may_resolve(user):
    auth.require_permission(user, auth.RESOLVE_EVENT, make_event(creator_id="u2"))


def test_other_member_may_not_resolve():
    with pytest.raises(AuthError, match="creator or the group admin"):
        auth.require_permission(MEMBER, auth.RESOLVE_EVENT, make_event(creator_id="u2"))


def test_resolve_needs_an_event():
    with pytest.raises(AuthError):
        auth.require_permission(ADMIN, auth.RESOLVE_EVENT)


@pytest.mark.parametrize("action", sorted(auth.ADMIN_ONLY_ACTIONS))
def test_admin_only_actions(action):
    auth.require_permission(ADMIN, action, make_event())
    auth.require_permission(ADMIN, action)
    for user in (CREATOR, MEMBER):
        with pytest.raises(AuthError, match="Only the group admin"):
            auth.require_permission(user, action, make_event())


def test_admin_of_other_group_has_no_rights_on_event():
    other_admin = make_user("u9", "admin", group_id="g2")
    for action in (auth.PLACE_BET, auth.RESOLVE_EVENT, auth.DELETE_EVENT):
        with pytest.raises(AuthError, match="Event not found"):
            auth.require_permission(other_admin, action, make_event())


def test_user_without_group_may_do_nothing():
    loner = make_user("u5", group_id=None)
    with pytest.raises(AuthError, match="group"):
        auth.require_permission(loner, auth.VIEW_FEED)


def test_unknown_action_is_rejected():
    with pytest.raises(AuthError, match="Unknown action"):
        auth.require_permission(ADMIN, "launch_rockets")


def test_auth_error_is_a_betting_error():
    assert issubclass(AuthError, BettingError)


# --- end to end with the service layer -----------------------------------------

def test_sign_up_join_bet_and_resolve(path):
    now = CLOSE - timedelta(hours=1)
    anna = auth.sign_up("Anna", PASSWORD, path)
    group = auth.create_group(anna.id, "Friends", Decimal("100"), path)
    auth.sign_up("Ben", PASSWORD, path)
    auth.sign_up("Cleo", PASSWORD, path)
    ben = auth.log_in("ben", PASSWORD, path)
    cleo = auth.log_in("Cleo", PASSWORD, path)
    auth.join_group(ben.id, group.invite_code, path)
    auth.join_group(cleo.id, group.invite_code, path)
    event = service.create_event(ben.id, "Anna is late", CLOSE, now, path=path)
    service.place_bet(anna.id, event.id, "no", 30, now, path)
    service.place_bet(ben.id, event.id, "yes", 10, now, path)
    with pytest.raises(AuthError, match="creator or the group admin"):
        service.resolve_event(cleo.id, event.id, "yes", CLOSE, path)
    service.resolve_event(ben.id, event.id, "yes", CLOSE, path)
    assert service.get_user(ben.id, path).balance == 130
    assert service.get_user(anna.id, path).balance == 70
