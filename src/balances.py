"""Top-ups, results, debt and the low-balance alert (requirements 2.4).

- Members join with 0. Only the admin tops up, choosing the amount each time;
  every member of the group gets the same amount, restricted members included.
  It is added to their balance and to what they received this period.
- A member's result this period is their balance minus what they received;
  they have debt when it is below 0.
- When a bet takes a member below 10% of the group's last top-up, the admin
  gets a notification (once, when the line is crossed).

Creating events at a balance of 0 is blocked in auth.require_permission().
Repayments (which start a new period) come next.
"""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Optional

from src import auth, storage
from src.auth import AuthError
from src.convert import cents_to_money, money_to_cents, topup_from_dict, topup_to_dict
from src.models import CENT, ROLE_ADMIN, TopUp
from src.notifications import add_notification

LOW_BALANCE_SHARE = Decimal("0.10")


def _clean_amount(amount) -> Decimal:
    """Return the top-up amount as Decimal cents, or raise AuthError.

    It must be a whole number or Decimal above 0 with at most 2 decimals.
    """
    if isinstance(amount, (bool, float)) or not isinstance(amount, (int, Decimal)):
        raise AuthError("Top-up must be a whole number or Decimal.")
    amount = Decimal(amount)
    if not amount.is_finite() or amount <= 0 or amount != amount.quantize(CENT):
        raise AuthError("Top-up must be above 0, with at most 2 decimals.")
    return amount.quantize(CENT)


def top_up(admin_id: str, group_id: str, amount, now: datetime, path=None) -> TopUp:
    """Give every member of the group the same amount and record the top-up.

    Only the group's admin may do this.
    """
    amount = _clean_amount(amount)
    with storage.transaction(path) as data:
        admin = auth.user_in_group(data, admin_id, group_id)
        auth.require_permission(admin, auth.TOP_UP)
        cents = money_to_cents(amount)
        for membership in data["memberships"]:
            if membership["group_id"] == group_id:
                membership["balance"] += cents
                membership["received"] += cents
        topup = TopUp(id=uuid.uuid4().hex, group_id=group_id, amount=amount,
                      created_by=admin_id, created_at=now)
        data["topups"].append(topup_to_dict(topup))
    return topup


def period_result(member) -> Decimal:
    """Return balance minus received this period (a Membership or a User).

    Above 0 the member is up, below 0 they are down. Stakes on open bets are
    already taken from the balance.
    """
    return member.balance - member.received


def has_debt(member) -> bool:
    """Return True if the member's balance is lower than what they received this period."""
    return period_result(member) < 0


def last_top_up(data: dict, group_id: str) -> Optional[TopUp]:
    """Return the group's newest top-up, or None if there has been none."""
    topups = [topup_from_dict(t) for t in data["topups"] if t["group_id"] == group_id]
    return max(topups, key=lambda t: t.created_at, default=None)


def low_balance_line(data: dict, group_id: str) -> Optional[Decimal]:
    """Return the balance below which a member is running low, or None without top-ups."""
    topup = last_top_up(data, group_id)
    return None if topup is None else (topup.amount * LOW_BALANCE_SHARE).quantize(CENT)


def notify_if_low(
    data: dict, group_id: str, user_id: str, spent: Decimal, now: datetime
) -> bool:
    """Notify the admin if spending `spent` just took the member below the low line.

    Call inside the transaction, after the balance was lowered. Returns True if
    the admin was notified. Members already below the line are not reported again.
    """
    line = low_balance_line(data, group_id)
    membership = next((m for m in data["memberships"]
                       if m["user_id"] == user_id and m["group_id"] == group_id), None)
    if line is None or membership is None:
        return False
    after = cents_to_money(membership["balance"])
    if not after < line <= after + spent:
        return False
    username = next(u["username"] for u in data["users"] if u["id"] == user_id)
    group_name = next(g["name"] for g in data["groups"] if g["id"] == group_id)
    text = (f"{username} is running low in {group_name} (below {line} coins). "
            "Choose a top-up or a repayment.")
    for admin in data["memberships"]:
        if admin["group_id"] == group_id and admin["role"] == ROLE_ADMIN:
            add_notification(data, admin["user_id"], group_id, text, now)
    return True
