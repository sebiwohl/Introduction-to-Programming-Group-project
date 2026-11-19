"""Confetti: a short shower over the screen when the user has won, with the
amount won in a small card in the middle ("YOU WON +15 COINS").

Which wins get confetti (once each, when the result is announced) is decided
in service.py; events.py shows it.

Pure HTML and CSS (no JavaScript), built as one line so st.markdown passes it
through. It sits on top of the page with pointer-events: none, so it never
blocks a click, falls for a few seconds and then disappears. Viewers who ask
their device for reduced motion get no confetti.

No Streamlit here, so it can be tested on its own.
"""

import html
import random

PIECES = 90
LENGTH = 4.0  # seconds until the last piece has fallen and the layer is gone
MAX_DELAY = 0.8  # pieces start within this many seconds, so it feels like one burst
# The app's theme: YES gold, a bright yellow, the orange accent and a lighter orange,
# NO orange-red and black.
COLOURS = ("#E0A100", "#FACC15", "#EA580C", "#FB923C", "#C8460F", "#0A0A0A")
AMOUNT_COLOUR = "#EA580C"  # the orange accent: readable on white, unlike the light gold

CSS = (
    '[data-testid="stElementContainer"]:has(.ci-confetti){position:absolute;width:0;height:0;margin:0}'
    # The page box fades and rises in (style.py, st-key-page_*). While a box has a transform
    # animation, a position:fixed child is placed inside that box instead of on the screen, so
    # the confetti would fill the whole tall page. While confetti shows, the page skips that.
    '[class*="st-key-page_"]:has(.ci-confetti){animation:none!important;transform:none!important}'
    ".ci-confetti{position:fixed;inset:0;z-index:1000000;pointer-events:none;overflow:hidden;"
    f"animation:ci-confetti-end {LENGTH}s linear forwards}}"
    "@keyframes ci-confetti-end{0%,90%{opacity:1}100%{opacity:0;visibility:hidden}}"
    ".ci-cf{position:absolute;top:-3vh;display:block;opacity:0;"
    "animation-name:ci-cf-fall;animation-timing-function:cubic-bezier(.3,.55,.55,1);"
    "animation-fill-mode:both}"
    "@keyframes ci-cf-fall{0%{opacity:1;transform:translate(0,0) rotate(0) rotateY(0)}"
    "100%{opacity:1;transform:translate(var(--dx),106vh) rotate(var(--r)) rotateY(var(--f))}}"
    # the amount won: a white card, so it stands out over the black "You called it" banner too
    ".ci-cf-card{position:absolute;left:50%;top:40%;display:flex;flex-direction:column;"
    "align-items:center;gap:4px;padding:18px 36px 16px;border-radius:18px;background:#FFFFFF;"
    "color:#0A0A0A;border:1px solid #F6E3D3;box-shadow:0 18px 50px rgba(154,52,18,.18);white-space:nowrap;"
    "font-family:Inter,system-ui,-apple-system,'Segoe UI',sans-serif;"
    f"animation:ci-cf-pop .45s cubic-bezier(.2,1.4,.4,1) both,ci-confetti-end {LENGTH}s linear forwards}}"
    ".ci-cf-card span{font-size:.72rem;font-weight:700;letter-spacing:.1em;color:#6B6B72}"
    f".ci-cf-card b{{font-size:3.2rem;font-weight:900;line-height:1;letter-spacing:-.02em;color:{AMOUNT_COLOUR}}}"
    ".ci-cf-card small{font-size:.7rem;font-weight:700;letter-spacing:.08em}"
    "@keyframes ci-cf-pop{from{opacity:0;transform:translate(-50%,-50%) scale(.6)}"
    "to{opacity:1;transform:translate(-50%,-50%) scale(1)}}"
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


def markup(won: str = "", seed: int = 7) -> str:
    """Return the confetti as one line of HTML with its own CSS.

    won is the amount won as text, like '+15'; it is shown in a card in the
    middle (no card when it is empty). The same seed gives the same shower.
    """
    rng = random.Random(seed)
    pieces = "".join(_piece(rng) for _ in range(PIECES))
    card = (f'<div class="ci-cf-card"><span>YOU WON</span><b>{html.escape(won)}</b>'
            "<small>COINS</small></div>") if won else ""
    return f'<style>{CSS}</style><div class="ci-confetti" aria-hidden="true">{card}{pieces}</div>'

