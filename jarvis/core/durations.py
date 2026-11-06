"""Durations as people say them: "пять минут", "полчаса", "через минуту",
"півтори години", "2 часа 30 минут", "an hour and a half".

Speech recognition writes small numbers as words as often as digits, and a
unit without a number means one ("через час", "на минуту"). Voice patterns
put DURATION into their regex (it has no capture groups of its own) and turn
the captured text into seconds with parse_duration().
"""

from __future__ import annotations

import re

# Numbers that are said as one word. Several spellings of the same number
# (gender, case, ru / uk / en) map to the same value.
_ONES: dict[str, float] = {
    # ru
    "один": 1, "одна": 1, "одну": 1, "два": 2, "две": 2, "три": 3, "четыре": 4,
    "пять": 5, "шесть": 6, "семь": 7, "восемь": 8, "девять": 9,
    # uk
    "дві": 2, "чотири": 4, "п'ять": 5, "шість": 6, "сім": 7, "вісім": 8, "дев'ять": 9,
    # en
    "one": 1, "a": 1, "an": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9,
}
_TEENS: dict[str, float] = {
    "десять": 10, "одиннадцать": 11, "двенадцать": 12, "тринадцать": 13,
    "четырнадцать": 14, "пятнадцать": 15, "шестнадцать": 16, "семнадцать": 17,
    "восемнадцать": 18, "девятнадцать": 19,
    "одинадцять": 11, "дванадцять": 12, "тринадцять": 13, "чотирнадцять": 14,
    "п'ятнадцять": 15, "шістнадцять": 16, "сімнадцять": 17, "вісімнадцять": 18,
    "дев'ятнадцять": 19,
    "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
}
_TENS: dict[str, float] = {
    "двадцать": 20, "тридцать": 30, "сорок": 40, "пятьдесят": 50, "шестьдесят": 60,
    "двадцять": 20, "тридцять": 30, "п'ятдесят": 50, "шістдесят": 60,
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
}
# "полтора часа", "півтори хвилини", "пару минут", "a couple of minutes".
_SPECIAL: dict[str, float] = {
    "полтора": 1.5, "полторы": 1.5, "півтора": 1.5, "півтори": 1.5,
    "пару": 2, "пара": 2, "couple of": 2, "a couple of": 2,
}
_APOSTROPHE = "['ʼ’]"


def _words(table: dict[str, float]) -> str:
    # Longest first, so "одиннадцать" is not read as "один" + "надцать".
    spelled = sorted(table, key=len, reverse=True)
    return "|".join(re.escape(w).replace("'", _APOSTROPHE) for w in spelled)


_NUMBER = (
    rf"(?:\d+(?:[.,]\d+)?|(?:{_words(_TENS)})(?:\s+(?:{_words(_ONES)}))?"
    rf"|{_words(_TEENS)}|{_words(_SPECIAL)}|{_words(_ONES)})"
)
_UNIT_SECONDS: tuple[tuple[str, int], ...] = (
    (r"час(?:а|ов)?|ч|годин[уиа]?|год|hours?|hrs?|h", 3600),
    (r"минут[уыа]?|мин|хвилин[уиа]?|хв|minutes?|mins?", 60),
    (r"секунд[уыа]?|сек|secs?|seconds?", 1),
)
_UNIT = "|".join(pattern for pattern, _ in _UNIT_SECONDS)
# A half on its own: "полчаса", "пів години", "полминуты", "half an hour".
_HALVES: tuple[tuple[str, int], ...] = (
    (r"полчаса|пів\s*години|half\s+an\s+hour", 1800),
    (r"полминуты|пів\s*хвилини|half\s+a\s+minute", 30),
)
_HALF = "|".join(pattern for pattern, _ in _HALVES)
_PART = rf"(?:{_HALF}|(?:{_NUMBER}\s*)?(?:{_UNIT})\b)"
_AND = r"(?:\s*,?\s*(?:и|і|й|та|and)?\s*)"

# A whole duration: one or more parts. No capture groups, so a pattern can
# wrap it in its own: rf"^таймер\s+на\s+({DURATION})$".
DURATION = rf"(?:{_PART}(?:{_AND}{_PART})*(?:\s+(?:and\s+a\s+half|с\s+половиной))?)"

_PART_RE = re.compile(
    rf"(?P<half>{_HALF})|(?:(?P<number>{_NUMBER})\s*)?(?P<unit>{_UNIT})\b", re.IGNORECASE
)
_AND_A_HALF = re.compile(r"(?:and\s+a\s+half|с\s+половиной)\s*$", re.IGNORECASE)


def _number(text: str | None) -> float:
    if not text:
        return 1  # "через минуту", "на час": a unit alone is one of it
    text = re.sub(_APOSTROPHE, "'", text.strip().lower())
    if re.fullmatch(r"\d+(?:[.,]\d+)?", text):
        return float(text.replace(",", "."))
    if text in _SPECIAL:
        return _SPECIAL[text]
    total = 0.0
    for word in text.split():
        total += _TENS.get(word) or _TEENS.get(word) or _ONES.get(word, 0)
    return total


def parse_duration(text: str) -> int | None:
    """Seconds in `text` (all of it a DURATION), or None when it is not one."""
    if not re.fullmatch(DURATION, text.strip(), re.IGNORECASE):
        return None
    seconds = 0.0
    last_unit = 0
    for m in _PART_RE.finditer(text):
        if m.group("half"):
            half = m.group("half").lower()
            seconds += next(s for p, s in _HALVES if re.fullmatch(p, half))
            continue
        unit = m.group("unit").lower()
        last_unit = next(s for p, s in _UNIT_SECONDS if re.fullmatch(p, unit))
        seconds += _number(m.group("number")) * last_unit
    if _AND_A_HALF.search(text) and last_unit:
        seconds += last_unit / 2  # "an hour and a half", "час с половиной"
    whole = int(round(seconds))
    return whole if whole > 0 else None
