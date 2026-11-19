"""Top-ups, results, debt and the low-balance alert (requirements 2.4).

- Members join with 0. Only the admin tops up, choosing the amount each time;
  every member of the group gets the same amount, restricted members included.
  It is added to their balance and to what they received this period.
- A member's result this period is their balance plus their stakes on open
  bets, minus what they received. An open stake is not lost yet: it moves
  into the next period with its bet. They have debt when the result is below 0.
- Repayment: everyone sees who is up and who is down and the simplified
  transfers. The real payment happens outside the app. Then the admin
  confirms "Repaid" with a top-up above 0: every balance becomes that top-up
  and a new period starts. Because open stakes carry over, the results of a
  period always add up to 0, so the transfers always match.
- When a bet takes a member below 10% of the group's last top-up, the admin
  gets a notification (once, when the line is crossed).

Creating events at a balance of 0 is blocked in auth.require_permission().
"""

import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Dict, List, Optional

from src import auth, storage
from src.auth import AuthError
from src.convert import (
    cents_to_money, money_to_cents, repayment_to_dict, topup_from_dict, topup_to_dict,
)
from src.models import (
    CENT, ROLE_ADMIN, STATUS_CLOSED, STATUS_OPEN, STATUS_PAUSED, Repayment, TopUp,
)
from src.notifications import add_notification

UNSETTLED = (STATUS_OPEN, STATUS_PAUSED, STATUS_CLOSED)

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
        return _record_top_up(data, group_id, amount, admin_id, now)


def _record_top_up(data: dict, group_id: str, amount: Decimal, admin_id: str,
                   now: datetime) -> TopUp:
    """Store a top-up record (the balances are changed by the caller); return it."""
    topup = TopUp(id=uuid.uuid4().hex, group_id=group_id, amount=amount,
                  created_by=admin_id, created_at=now)
    data["topups"].append(topup_to_dict(topup))
    return topup


def period_result(member, open_stake: Decimal = Decimal("0")) -> Decimal:
    """Return balance + open stakes - received this period (a Membership or a User).

    Above 0 the member is up, below 0 they are down. open_stake is the total of
    their stakes on bets that are not settled yet: those coins are not lost yet.
    """
    return member.balance + open_stake - member.received


def has_debt(member, open_stake: Decimal = Decimal("0")) -> bool:
    """Return True if the member's result this period is below 0."""
    return period_result(member, open_stake) < 0


def open_stakes(data: dict, group_id: str) -> Dict[str, int]:
    """Return {user id: cents} staked on the group's bets that are not settled yet."""
    unsettled = {e["id"] for e in data["events"]
                 if e["group_id"] == group_id and e["status"] in UNSETTLED}
    stakes: Dict[str, int] = {}
    for bet in data["bets"]:
        if bet["event_id"] in unsettled and bet["payout"] is None:
            stakes[bet["user_id"]] = stakes.get(bet["user_id"], 0) + bet["stake"]
    return stakes


# --- repayment ------------------------------------------------------------------

@dataclass
class MemberResult:
    """One member's line in the repayment window."""

    user_id: str
    username: str
    received: Decimal
    balance: Decimal
    open_stake: Decimal
    result: Decimal


@dataclass
class Transfer:
    """One payment outside the app: payer gives receiver the amount."""

    payer: str
    receiver: str
    amount: Decimal


@dataclass
class RepaymentSummary:
    """What the repayment window shows: results, transfers and the text to copy."""

    group_name: str
    results: List[MemberResult]
    transfers: List[Transfer]
    text: str


def _member_results(data: dict, group_id: str) -> List[MemberResult]:
    """Return every member's result this period, biggest winner first."""
    names = {u["id"]: u["username"] for u in data["users"]}
    stakes = open_stakes(data, group_id)
    rows = []
    for m in data["memberships"]:
        if m["group_id"] != group_id:
            continue
        stake = cents_to_money(stakes.get(m["user_id"], 0))
        balance, received = cents_to_money(m["balance"]), cents_to_money(m["received"])
        rows.append(MemberResult(m["user_id"], names[m["user_id"]], received, balance,
                                 stake, balance + stake - received))
    return sorted(rows, key=lambda r: (-r.result, r.username.casefold()))


def simplify_transfers(results: List[MemberResult]) -> List[Transfer]:
    """Return few payments that settle everyone: the biggest debtor pays the biggest winner.

    Each step settles at least one person fully, so there are fewer transfers
    than members. If the results do not add up to 0 (they always should), the
    part that cannot be matched is left out.
    """
    debtors = sorted(([r.username, -r.result] for r in results if r.result < 0),
                     key=lambda d: (-d[1], d[0].casefold()))
    winners = sorted(([r.username, r.result] for r in results if r.result > 0),
                     key=lambda w: (-w[1], w[0].casefold()))
    transfers, d, w = [], 0, 0
    while d < len(debtors) and w < len(winners):
        amount = min(debtors[d][1], winners[w][1])
        transfers.append(Transfer(debtors[d][0], winners[w][0], amount))
        debtors[d][1] -= amount
        winners[w][1] -= amount
        d += debtors[d][1] == 0
        w += winners[w][1] == 0
    return transfers


def _copy_text(group_name: str, transfers: List[Transfer]) -> str:
    """Return the message to paste into the group chat."""
    if not transfers:
        return f"Called it! {group_name}: everyone is even, nobody owes anything."
    lines = [f"- {t.payer} pays {t.receiver} {t.amount} coins" for t in transfers]
    return f"Called it! repayment for {group_name}:\n" + "\n".join(lines)


def repayment_summary(user_id: str, group_id: str, path=None) -> RepaymentSummary:
    """Return the repayment window for a member of the group; it changes nothing."""
    data = storage.load_data(path)
    member = auth.user_in_group(data, user_id, group_id)
    auth.require_permission(member, auth.VIEW_FEED)
    group_name = next(g["name"] for g in data["groups"] if g["id"] == group_id)
    results = _member_results(data, group_id)
    transfers = simplify_transfers(results)
    return RepaymentSummary(group_name, results, transfers, _copy_text(group_name, transfers))


def confirm_repaid(admin_id: str, group_id: str, amount, now: datetime, path=None) -> Repayment:
    """Start a new period after the members have paid each other outside the app.

    Only the admin. amount is the top-up everyone gets in the same step (above
    0). Every balance becomes that amount. Open bets carry over: each member's
    open stakes move into the new period's "received", so the next period adds
    up too. Everyone is notified.
    """
    amount = _clean_amount(amount)
    cents = money_to_cents(amount)
    with storage.transaction(path) as data:
        admin = auth.user_in_group(data, admin_id, group_id)
        auth.require_permission(admin, auth.REPAY)
        stakes = open_stakes(data, group_id)
        for membership in data["memberships"]:
            if membership["group_id"] == group_id:
                membership["balance"] = cents
                membership["received"] = cents + stakes.get(membership["user_id"], 0)
                add_notification(data, membership["user_id"], group_id,
                                 f"Repaid: a new period started. Everyone got {amount} coins.",
                                 now)
        _record_top_up(data, group_id, amount, admin_id, now)
        repayment = Repayment(id=uuid.uuid4().hex, group_id=group_id, created_by=admin_id,
                              created_at=now)
        data["repayments"].append(repayment_to_dict(repayment))
    return repayment


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
