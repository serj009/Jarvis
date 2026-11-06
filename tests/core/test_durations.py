"""Durations as people say them (jarvis.core.durations).

Speech recognition writes "пять минут" as often as "5 минут", and "через
час" has no number at all; the timer and reminder patterns understood only
digits, so these went to the model.
"""

from __future__ import annotations

import re

import pytest

from jarvis.core.durations import CLOCK, DURATION, parse_clock, parse_duration


@pytest.mark.parametrize(
    ("text", "seconds"),
    [
        ("5 минут", 300),
        ("пять минут", 300),
        ("одну минуту", 60),
        ("минуту", 60),
        ("час", 3600),
        ("полчаса", 1800),
        ("полтора часа", 5400),
        ("час с половиной", 5400),
        ("2 часа 30 минут", 9000),
        ("два часа и тридцать минут", 9000),
        ("двадцать пять секунд", 25),
        ("одиннадцать минут", 660),
        ("пару минут", 120),
        ("1,5 часа", 5400),
        ("10 сек", 10),
        ("п'ять хвилин", 300),
        ("п’ятнадцять хвилин", 900),
        ("годину", 3600),
        ("півтори години", 5400),
        ("пів години", 1800),
        ("півхвилини", 30),
        ("an hour", 3600),
        ("half an hour", 1800),
        ("an hour and a half", 5400),
        ("twenty five minutes", 1500),
        ("a couple of minutes", 120),
        ("90 seconds", 90),
    ],
)
def test_parse_duration(text, seconds):
    assert parse_duration(text) == seconds


@pytest.mark.parametrize("text", ["купить хлеб", "пять", "минут купить", "", "0 минут"])
def test_not_a_duration(text):
    assert parse_duration(text) is None


def test_duration_has_no_groups_of_its_own():
    """Patterns wrap DURATION in their own group and count on the numbering."""
    assert re.compile(DURATION).groups == 0


# -- clock times ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "clock"),
    [
        ("15:00", "15:00"),
        ("9.30", "09:30"),
        ("10", "10:00"),
        ("десять", "10:00"),
        ("десять тридцать", "10:30"),
        ("десять ноль пять", "10:05"),
        ("восемь вечера", "20:00"),
        ("три часа дня", "15:00"),
        ("девять утра", "09:00"),
        ("двенадцать ночи", "00:00"),
        ("двадцать два пятнадцать", "22:15"),
        ("10 вечера", "22:00"),
        ("десятій", "10:00"),
        ("дев'ятій", "09:00"),
        ("seven pm", "19:00"),
        ("ten thirty", "10:30"),
    ],
)
def test_parse_clock(text, clock):
    assert parse_clock(text) == clock


@pytest.mark.parametrize("text", ["25", "10:75", "купить", "a"])
def test_not_a_clock_time(text):
    assert parse_clock(text) is None


def test_clock_has_no_groups_of_its_own():
    assert re.compile(CLOCK).groups == 0
