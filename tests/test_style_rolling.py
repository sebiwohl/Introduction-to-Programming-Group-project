"""Tests for the rolling-number markup on the Profile page (src/ui/style.py)."""

import re

from src.ui import style


def test_rolling_stops_each_digit_on_its_value_and_keeps_tags():
    markup = style.rolling('<span class="ci-yes">+1,250</span>')
    targets = [int(d) - 10 for d in re.findall(r"--d:(\d+)", markup)]
    assert targets == [1, 2, 5, 0]
    assert markup.startswith('<span class="ci-yes">') and markup.endswith("</span>")
    assert '<span aria-hidden="true">,</span>' in markup


def test_rolling_leaves_text_without_digits_alone():
    assert "ci-roll" not in style.rolling("—")


def test_plain_text_removes_tags():
    assert style.plain_text('<span class="ci-yes">W3</span>') == "W3"


def test_rank_badge_gives_medals_to_the_top_three_only():
    assert "ci-medal-gold" in style.rank_badge(1)
    assert "ci-medal-silver" in style.rank_badge(2)
    assert "ci-medal-bronze" in style.rank_badge(3)
    assert "ci-medal-plain" in style.rank_badge(4)


def test_shuffle_starts_rows_in_reverse_order():
    # 5 rows: the first row starts 4 rows down, the middle one stays, the last starts 4 rows up
    offsets = [re.search(r"--from:(-?\d+)", style.shuffle_style(i, 5)).group(1) for i in range(5)]
    assert offsets == ["4", "2", "0", "-2", "-4"]


def test_each_ranking_gets_its_own_animation_so_switching_replays_it():
    names = {re.search(r"animation-name:(\S+)", style.shuffle_style(0, 3, v)).group(1)
             for v in range(4)}
    assert names == {"ci-shuffle-0", "ci-shuffle-1", "ci-shuffle-2", "ci-shuffle-3"}


def test_trophy_has_the_medal_colour_and_the_rank():
    """The podium trophy is gold, silver or bronze and shows the rank on the cup."""
    assert "#F59E0B" in style.trophy(1) and ">1</text>" in style.trophy(1)
    assert "#94A3B8" in style.trophy(2) and ">2</text>" in style.trophy(2)
    assert "#C2722E" in style.trophy(3) and ">3</text>" in style.trophy(3)
    assert "ci-trophy-plain" in style.trophy(4)  # below the podium: a plain grey cup
