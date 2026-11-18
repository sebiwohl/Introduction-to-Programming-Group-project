"""Core betting logic: pool, odds, placing bets and resolving events.

Two kinds of event share the same pari-mutuel pool and rounding rule: yes/no
events, and names events whose options are names (people, for example). The
names functions sit in their own section below.

Pure functions only: no Streamlit, no file access. Money is a Decimal with at
most 2 decimal places (cents). Floats are never used for money.
"""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Dict, List, Optional, Union

from src.models import (
    CENT,
    KIND_NAMES,
    MAX_OPTION_LENGTH,
    MAX_OPTIONS,
    MIN_OPTIONS,
    SIDES,
    STATUS_CLOSED,
    STATUS_OPEN,
    STATUS_RESOLVED,
    Bet,
    Event,
    Option,
    User,
)

Amount = Union[int, Decimal]


class BettingError(Exception):
    """Raised when a bet or a resolution breaks a rule. Nothing is changed."""


def _check_side(side: str) -> None:
    """Raise BettingError if side is not 'yes' or 'no'."""
    if side not in SIDES:
        raise BettingError(f"Side must be one of {SIDES}, got {side!r}.")


def user_group_ids(user: User) -> set:
    """Return the ids of all groups the user belongs to.

    Version 1 has one group per user. When multiple groups are added, change
    only this function and every access check follows.
    """
    return {user.group_id} if user.group_id else set()


def can_access_event(user: User, event: Event) -> bool:
    """Return True if the event belongs to one of the user's groups."""
    return event.group_id in user_group_ids(user)


def visible_events(user: User, events: List[Event]) -> List[Event]:
    """Return only the events the user may see (those in the user's groups)."""
    return [event for event in events if can_access_event(user, event)]


def _to_cents(amount: Amount) -> int:
    """Convert an amount with at most 2 decimals to a whole number of cents."""
    return int(Decimal(amount) * 100)


def _from_cents(cents: int) -> Decimal:
    """Convert a whole number of cents back to a 2-decimal amount."""
    return (Decimal(cents) / 100).quantize(CENT)


def pool(bets: List[Bet]) -> Decimal:
    """Return the total of all stakes on both sides."""
    return sum((Decimal(bet.stake) for bet in bets), Decimal("0"))


def stake_per_side(bets: List[Bet]) -> Dict[str, Decimal]:
    """Return the total stake on each side, e.g. {'yes': 40, 'no': 10}."""
    totals = {side: Decimal("0") for side in SIDES}
    for bet in bets:
        _check_side(bet.side)
        totals[bet.side] += Decimal(bet.stake)
    return totals


def odds(bets: List[Bet], side: str) -> Optional[float]:
    """Return pool / stake on `side` for display, or None if that side is empty."""
    _check_side(side)
    side_stake = stake_per_side(bets)[side]
    if side_stake == 0:
        return None
    return float(pool(bets) / side_stake)


def split_pool(total_pool: Amount, stakes: List[Amount]) -> List[Decimal]:
    """Split a pool among winners to 2 decimals; the result sums to total_pool.

    Rounding and leftover rule (the only place it lives): each winner gets
    total_pool * stake / sum(stakes), rounded down to the cent. The leftover
    cents go to the winner with the largest stake; on a tie, the earliest one
    in `stakes`. Works in whole cents internally, so it is exact.
    """
    stake_cents = [_to_cents(stake) for stake in stakes]
    winning_total = sum(stake_cents)
    if winning_total <= 0:
        raise BettingError("Cannot split a pool without winning stakes.")
    pool_cents = _to_cents(total_pool)
    payouts = [pool_cents * stake // winning_total for stake in stake_cents]
    leftover = pool_cents - sum(payouts)
    biggest = max(range(len(stake_cents)), key=lambda i: (stake_cents[i], -i))
    payouts[biggest] += leftover
    return [_from_cents(cents) for cents in payouts]


def _validate_stake(stake: Amount) -> Decimal:
    """Return the stake as a Decimal, or raise BettingError if it is invalid.

    A stake must be an int or Decimal (not a float), finite, positive and have
    at most 2 decimal places.
    """
    if isinstance(stake, bool) or not isinstance(stake, (int, Decimal)):
        raise BettingError("Stake must be a number with at most 2 decimals.")
    stake = Decimal(stake)
    if not stake.is_finite():
        raise BettingError("Stake must be a finite number.")
    if stake <= 0:
        raise BettingError("Stake must be positive.")
    if stake != stake.quantize(CENT):
        raise BettingError("Stake can have at most 2 decimals.")
    return stake.quantize(CENT)


def place_bet(user: User, event: Event, side: str, stake: Amount, now: datetime) -> Bet:
    """Validate a bet, deduct the stake from the user and return the new Bet.

    Raises BettingError (and changes nothing) if the event is not in one of the
    user's groups, the event is not open or is past its close time, the side is
    invalid (yes or no, or one of the options of a names event), or the stake
    is not a positive amount with at most 2 decimals within the user's balance.
    """
    if not can_access_event(user, event):
        raise BettingError("You can only bet on events in your own group.")
    if event.status != STATUS_OPEN:
        raise BettingError(f"Event is {event.status}, not open for bets.")
    if now >= event.close_time:
        raise BettingError("Event is past its close time.")
    _check_bet_side(event, side)
    stake = _validate_stake(stake)
    if stake > user.balance:
        raise BettingError("Stake exceeds balance.")
    user.balance = Decimal(user.balance) - stake
    return Bet(
        id=uuid.uuid4().hex,
        event_id=event.id,
        user_id=user.id,
        side=side,
        stake=stake,
    )


def _check_bet_side(event: Event, side: str) -> None:
    """Raise BettingError unless `side` fits the event: yes or no, or one of its options."""
    if is_names_event(event):
        if side not in option_ids(event):
            raise BettingError("Pick one of the event's options.")
    else:
        _check_side(side)


def _check_resolvable(event: Event, now: datetime) -> None:
    """Raise BettingError unless the event may get its result now."""
    if event.status == STATUS_RESOLVED:
        raise BettingError("Event is already resolved and read-only.")
    if event.status not in (STATUS_OPEN, STATUS_CLOSED):
        raise BettingError(f"Cannot resolve an event that is {event.status}.")
    if now < event.close_time:
        raise BettingError("Event cannot be resolved before its close time.")


def _check_bets_belong(event: Event, bets: List[Bet], users: Dict[str, User]) -> None:
    """Raise BettingError if a bet is on another event or by an unknown user."""
    for bet in bets:
        if bet.event_id != event.id:
            raise BettingError(f"Bet {bet.id} belongs to another event.")
        if bet.user_id not in users:
            raise BettingError(f"Unknown user {bet.user_id} on bet {bet.id}.")


def _compute_payouts(bets: List[Bet], outcome: str) -> List[Decimal]:
    """Return one payout per bet: refunds, or winners splitting the pool."""
    totals = stake_per_side(bets)
    only_one_side = any(stake == 0 for stake in totals.values())
    if only_one_side:  # also covers "nobody backed the winner" and no bets
        return [Decimal(bet.stake).quantize(CENT) for bet in bets]
    winners = [bet for bet in bets if bet.side == outcome]
    winner_payouts = iter(split_pool(pool(bets), [bet.stake for bet in winners]))
    zero = Decimal("0").quantize(CENT)
    return [next(winner_payouts) if bet.side == outcome else zero for bet in bets]


def resolve_event(
    event: Event,
    bets: List[Bet],
    outcome: str,
    users: Dict[str, User],
    now: datetime,
) -> Decimal:
    """Set the outcome, pay out every bet and mark the event resolved.

    Only possible at or after the close time. Winners split the whole pool in
    proportion to their stakes. If only one side has bets (so nobody backed the
    winner, or nobody lost), every stake is refunded. Sets Bet.payout and
    credits the users' balances. Returns the total paid out, which always
    equals the pool. A resolved event is read-only, so resolving it again
    raises BettingError. Names events are resolved with resolve_names_event.
    Nothing changes on error.
    """
    if is_names_event(event):
        raise BettingError("Pick the winning option of this names event.")
    _check_resolvable(event, now)
    _check_side(outcome)
    _check_bets_belong(event, bets, users)

    payouts = _compute_payouts(bets, outcome)
    total_paid = sum(payouts, Decimal("0"))
    if total_paid != pool(bets):  # safety net; should never happen
        raise BettingError("Payouts do not add up to the pool; nothing was paid.")
    for bet, payout in zip(bets, payouts):
        bet.payout = payout
        user = users[bet.user_id]
        user.balance = Decimal(user.balance) + payout
    event.outcome = outcome
    event.status = STATUS_RESOLVED
    return total_paid


# --- names events -----------------------------------------------------------

def is_names_event(event: Event) -> bool:
    """Return True for an event whose options are names instead of yes and no."""
    return event.kind == KIND_NAMES


def clean_options(names: List[str]) -> List[str]:
    """Return the option names trimmed, or raise BettingError if they break a rule.

    A names event needs MIN_OPTIONS to MAX_OPTIONS options. Each name is
    trimmed, cannot be empty, has at most MAX_OPTION_LENGTH characters, and no
    two names may match ignoring case.
    """
    if not isinstance(names, (list, tuple)) or not all(isinstance(n, str) for n in names):
        raise BettingError("Options must be a list of names.")
    cleaned = [name.strip() for name in names]
    if not MIN_OPTIONS <= len(cleaned) <= MAX_OPTIONS:
        raise BettingError(
            f"A names event needs {MIN_OPTIONS} to {MAX_OPTIONS} names, got {len(cleaned)}."
        )
    if not all(cleaned):
        raise BettingError("Names cannot be empty.")
    if any(len(name) > MAX_OPTION_LENGTH for name in cleaned):
        raise BettingError(f"Names can be at most {MAX_OPTION_LENGTH} characters.")
    seen = set()
    for name in cleaned:
        if name.casefold() in seen:
            raise BettingError(f"“{name}” is listed twice.")
        seen.add(name.casefold())
    return cleaned


def make_options(names: List[str]) -> List[Option]:
    """Return checked options with simple ids in order: 'o1', 'o2', ..."""
    return [Option(id=f"o{number}", name=name)
            for number, name in enumerate(clean_options(names), start=1)]


def option_ids(event: Event) -> List[str]:
    """Return the ids of a names event's options, in the order they were listed."""
    return [option.id for option in event.options]


def side_label(event: Event, side: str) -> str:
    """Return how a side is shown: the option's name, or YES / NO."""
    if is_names_event(event):
        return next((o.name for o in event.options if o.id == side), "Unknown option")
    return side.upper()


def stake_per_option(bets: List[Bet], event: Event) -> Dict[str, Decimal]:
    """Return the total stake on each option of a names event, e.g. {'o1': 30, 'o2': 0}."""
    totals = {option_id: Decimal("0") for option_id in option_ids(event)}
    for bet in bets:
        if bet.side not in totals:
            raise BettingError(f"Bet {bet.id} is on an option this event does not have.")
        totals[bet.side] += Decimal(bet.stake)
    return totals


def option_odds(bets: List[Bet], event: Event, option_id: str) -> Optional[float]:
    """Return pool / stake on one option for display, or None if nobody backed it."""
    stakes = stake_per_option(bets, event)
    if option_id not in stakes:
        raise BettingError("Unknown option.")
    if stakes[option_id] == 0:
        return None
    return float(pool(bets) / stakes[option_id])


def pool_share(bets: List[Bet], event: Event, option_id: str) -> Optional[float]:
    """Return the share of the pool on one option (0 to 1), or None while there are no bets."""
    stakes = stake_per_option(bets, event)
    if option_id not in stakes:
        raise BettingError("Unknown option.")
    total = pool(bets)
    return float(stakes[option_id] / total) if total else None


def paid_odds(bets: List[Bet], winners: List[str]) -> Optional[float]:
    """Return what each coin on a winning option paid (pool / winning stake), or None if refunded."""
    winning = sum((Decimal(bet.stake) for bet in bets if bet.side in winners), Decimal("0"))
    return float(pool(bets) / winning) if winning else None


def _check_winners(event: Event, winners: List[str]) -> List[str]:
    """Return the winning option ids in the event's order, or raise BettingError."""
    if isinstance(winners, str) or not isinstance(winners, (list, tuple, set, frozenset)):
        raise BettingError("Winners must be a list of options.")
    chosen = set(winners)
    if not chosen:
        raise BettingError("Pick at least one winning option.")
    if not chosen <= set(option_ids(event)):
        raise BettingError("Pick the winners from the event's options.")
    if len(chosen) > 1 and not event.multi_winner:
        raise BettingError("This event has one winner: pick exactly one option.")
    return [option_id for option_id in option_ids(event) if option_id in chosen]


def names_payouts(bets: List[Bet], winners: List[str]) -> List[Decimal]:
    """Return one payout per bet on a names event.

    The whole pool goes to the bets on the winning option(s), in proportion to
    their stakes: payout = stake x pool / stake on the winning options, with
    the same rounding rule as yes/no (split_pool). If nobody bet on a winning
    option, every stake is refunded.
    """
    winning = [bet for bet in bets if bet.side in winners]
    if not winning:
        return [Decimal(bet.stake).quantize(CENT) for bet in bets]
    shares = iter(split_pool(pool(bets), [bet.stake for bet in winning]))
    zero = Decimal("0").quantize(CENT)
    return [next(shares) if bet.side in winners else zero for bet in bets]


def resolve_names_event(
    event: Event,
    bets: List[Bet],
    winners: List[str],
    users: Dict[str, User],
    now: datetime,
) -> Decimal:
    """Set the winning option(s) of a names event, pay out and mark it resolved.

    Same timing rules as resolve_event. A one-winner event needs exactly one
    winner; a multi_winner event takes one or more. Sets Bet.payout, credits
    the balances and returns the total paid out, which always equals the pool.
    Nothing changes on error.
    """
    if not is_names_event(event):
        raise BettingError("This is a yes/no event: set the result to yes or no.")
    _check_resolvable(event, now)
    chosen = _check_winners(event, winners)
    _check_bets_belong(event, bets, users)
    stake_per_option(bets, event)  # every bet must be on one of the options
    payouts = names_payouts(bets, chosen)
    total_paid = sum(payouts, Decimal("0"))
    if total_paid != pool(bets):  # safety net; should never happen
        raise BettingError("Payouts do not add up to the pool; nothing was paid.")
    for bet, payout in zip(bets, payouts):
        bet.payout = payout
        user = users[bet.user_id]
        user.balance = Decimal(user.balance) + payout
    event.winners = chosen
    event.status = STATUS_RESOLVED
    return total_paid
