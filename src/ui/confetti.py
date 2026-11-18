"""Confetti: a short shower over the screen when the user's latest bet won.

Pure HTML and CSS (no JavaScript), built as one line so st.markdown passes it
through. It sits on top of the page with pointer-events: none, so it never
blocks a click, falls for a few seconds and then disappears. Viewers who ask
their device for reduced motion get no confetti.

No Streamlit here, so it can be tested on its own; events.py shows it.
"""

import random
from typing import Iterable, List, Optional

from src import stats
from src.service import BetRecord

PIECES = 90
LENGTH = 3.6  # seconds until the last piece has fallen and the layer is gone
MAX_DELAY = 0.8  # pieces start within this many seconds, so it feels like one burst
# The app's YES blue and NO orange, a gold and black, plus lighter versions.
COLOURS = ("#3B3BE6", "#C8460F", "#FFC93C", "#0A0A0A", "#8C8CF5", "#F2A07A")

CSS = (
    '[data-testid="stElementContainer"]:has(.ci-confetti){position:absolute;width:0;height:0;margin:0}'
    ".ci-confetti{position:fixed;inset:0;z-index:1000000;pointer-events:none;overflow:hidden;"
    f"animation:ci-confetti-end {LENGTH}s linear forwards}}"
    "@keyframes ci-confetti-end{0%,90%{opacity:1}100%{opacity:0;visibility:hidden}}"
    ".ci-cf{position:absolute;top:-3vh;display:block;opacity:0;"
    "animation-name:ci-cf-fall;animation-timing-function:cubic-bezier(.3,.55,.55,1);"
    "animation-fill-mode:both}"
    "@keyframes ci-cf-fall{0%{opacity:1;transform:translate(0,0) rotate(0) rotateY(0)}"
    "100%{opacity:1;transform:translate(var(--dx),106vh) rotate(var(--r)) rotateY(var(--f))}}"
    "@media (prefers-reduced-motion:reduce){.ci-confetti{display:none}}"
)


def _piece(rng: random.Random) -> str:
    """Return one paper piece: a random spot, colour, size, shape, drift, spin and timing."""
    delay = rng.uniform(0, MAX_DELAY)
    fall = rng.uniform(2.0, LENGTH - MAX_DELAY - 0.2)
    width = rng.uniform(0.6, 1.1)
    height = width * (1 if rng.random() < 0.25 else rng.uniform(1.6, 2.4))
    radius = "50%" if height == width else "2px"
    return (
        f'<span class="ci-cf" style="left:{rng.uniform(0, 100):.1f}%;width:{width:.2f}vmin;'
        f"height:{height:.2f}vmin;border-radius:{radius};background:{rng.choice(COLOURS)};"
        f"--dx:{rng.uniform(-14, 14):.1f}vw;--r:{rng.choice((-1, 1)) * rng.uniform(240, 720):.0f}deg;"
        f"--f:{rng.uniform(360, 1080):.0f}deg;animation-duration:{fall:.2f}s;"
        f'animation-delay:{delay:.2f}s"></span>'
    )


def markup(seed: int = 7) -> str:
    """Return the confetti as one line of HTML with its own CSS (the same seed gives the same shower)."""
    rng = random.Random(seed)
    pieces = "".join(_piece(rng) for _ in range(PIECES))
    return f'<style>{CSS}</style><div class="ci-confetti" aria-hidden="true">{pieces}</div>'


def win_to_celebrate(records: List[BetRecord], shown: Iterable[str]) -> Optional[str]:
    """Return the id of the user's latest winning bet if it has no confetti yet, else None.

    Records come newest event first (service._bet_history); refunds are skipped
    and only a win counts (stats.latest_win). `shown` holds the bet ids that
    already had their confetti in this session.
    """
    record = stats.latest_win(records)
    if record is None or record.bet.id in set(shown):
        return None
    return record.bet.id
