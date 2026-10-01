"""Known "open" targets: one spoken name -> desktop app and/or website.

Standard (decided 2026-10-01): open the installed desktop app first; if it
is not installed, open the website in the default browser. "in browser" /
"website" forces the website, "app" / "application" asks for the app (and
still falls back to the website when the app is missing).

Matching is deliberately EXACT, not fuzzy:
- a spoken name must be one of the target's `names` (Russian, Ukrainian,
  English spellings, common Whisper variants);
- an app counts as installed only if one of the target's `apps` keys is in
  the installed-apps index. Fuzzy matching would turn "google" into
  "Google Chrome" (token overlap ~0.95).

Pure logic, no I/O: the caller passes `find_app`, so this is testable
without Windows. Anything that does not match returns None and is left to
the LLM, exactly as before.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Literal

Mode = Literal["auto", "web", "app"]


@dataclass(frozen=True, slots=True)
class OpenTarget:
    key: str
    display: str
    # Spoken names, any language. Compared after _normalize, spaces ignored.
    names: tuple[str, ...]
    # Installed-apps index keys (normalize_query form: lowercase latin).
    apps: tuple[str, ...] = ()
    url: str | None = None


@dataclass(frozen=True, slots=True)
class OpenPlan:
    target: OpenTarget
    kind: Literal["app", "url"]
    value: str  # installed-apps index key, or the URL
    mode: Mode


# Spaces are ignored when matching ("гит хаб" == "гитхаб"), so list each
# spelling once without its spaced variant.
DEFAULT_TARGETS: tuple[OpenTarget, ...] = (
    OpenTarget("youtube", "YouTube",
               ("youtube", "ютуб", "ютюб", "ютуба"),
               url="https://www.youtube.com"),
    OpenTarget("github", "GitHub",
               ("github", "гитхаб", "гітхаб", "гитхаба", "гітхабу"),
               apps=("github desktop",), url="https://github.com"),
    OpenTarget("telegram", "Telegram",
               ("telegram", "телеграм", "телеграмм", "телеграма", "телеграмму"),
               apps=("telegram desktop", "telegram"), url="https://web.telegram.org"),
    OpenTarget("google", "Google",
               ("google", "гугл", "гугла"),
               url="https://www.google.com"),
    OpenTarget("gmail", "Gmail",
               ("gmail", "джимейл", "гмейл", "гугл почта", "гугл пошта", "гугл почту"),
               url="https://mail.google.com"),
    OpenTarget("wikipedia", "Wikipedia",
               ("wikipedia", "википедия", "википедию", "вікіпедія", "вікіпедію"),
               url="https://www.wikipedia.org"),
    OpenTarget("discord", "Discord",
               ("discord", "дискорд", "діскорд", "дискорда"),
               apps=("discord",), url="https://discord.com/app"),
    OpenTarget("steam", "Steam",
               ("steam", "стим", "стім", "стима"),
               apps=("steam",), url="https://store.steampowered.com"),
    OpenTarget("spotify", "Spotify",
               ("spotify", "спотифай", "спотіфай"),
               apps=("spotify",), url="https://open.spotify.com"),
    OpenTarget("reddit", "Reddit",
               ("reddit", "реддит", "редит", "реддіт"),
               url="https://www.reddit.com"),
    OpenTarget("twitch", "Twitch",
               ("twitch", "твич", "твіч"),
               url="https://www.twitch.tv"),
    OpenTarget("netflix", "Netflix",
               ("netflix", "нетфликс", "нетфлікс"),
               apps=("netflix",), url="https://www.netflix.com"),
    OpenTarget("chatgpt", "ChatGPT",
               ("chatgpt", "чат гпт", "чат джипити"),
               apps=("chatgpt",), url="https://chatgpt.com"),
)

# Leading verb. Includes the forms Whisper actually produced in live logs
# ("Открою", "Открои").
_VERB = re.compile(
    r"^(?:открой|открою|открои|откройте|открыть|запусти|запустите|запустить"
    r"|відкрий|відкрию|відкрийте|відкрити|запустіть|запустити"
    r"|open|launch|start|run)\b\s*"
)
_LEADING_FILLER = re.compile(r"^(?:эй\s+|hey\s+)?(?:джарвис|джарвіс|jarvis)\s+")

# Phrases that pick the mode. Longest first so "in the browser" wins over
# "browser". Matched as whole words anywhere after the verb.
_WEB_PHRASES = (
    "in the browser", "in a browser", "in browser", "через браузер",
    "в браузере", "в браузер", "в браузері", "у браузері", "у браузер",
    "website", "web site", "сайт", "сайте", "сайта", "site", "веб",
)
_APP_PHRASES = (
    "the app", "application", "app", "desktop",
    "приложение", "приложения", "программу", "программа", "прогу",
    "застосунок", "застосунку", "додаток", "програму", "програма",
)
_FILLERS = (
    "пожалуйста", "будь ласка", "please", "мне", "мені", "for me",
)


def _normalize(text: str) -> str:
    """Lowercase, ё->е, punctuation to spaces, collapse whitespace."""
    s = text.lower().replace("ё", "е")
    s = re.sub(r"[^\w]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _strip_phrases(text: str, phrases: Iterable[str]) -> tuple[str, bool]:
    found = False
    for phrase in sorted(phrases, key=len, reverse=True):
        new = re.sub(rf"(?:^|\s){re.escape(phrase)}(?=\s|$)", " ", text)
        if new != text:
            found = True
            text = re.sub(r"\s+", " ", new).strip()
    return text, found


def _compact(text: str) -> str:
    return text.replace(" ", "")


def parse_open_command(
    text: str, targets: Iterable[OpenTarget] = DEFAULT_TARGETS
) -> tuple[OpenTarget, Mode] | None:
    """('Открой стим в браузере') -> (steam target, 'web'); None if the
    utterance is not "<verb> <known target> [mode words]"."""
    s = _normalize(text)
    s = _LEADING_FILLER.sub("", s)
    m = _VERB.match(s)
    if not m:
        return None
    rest = s[m.end():]
    rest, _ = _strip_phrases(rest, _FILLERS)
    rest, web = _strip_phrases(rest, _WEB_PHRASES)
    rest, app = _strip_phrases(rest, _APP_PHRASES)
    name = _compact(rest)
    if not name:
        return None
    mode: Mode = "web" if web else "app" if app else "auto"
    for target in targets:
        if any(_compact(_normalize(n)) == name for n in target.names):
            return target, mode
    return None


def plan_open(
    text: str,
    find_app: Callable[[str], bool],
    targets: Iterable[OpenTarget] = DEFAULT_TARGETS,
) -> OpenPlan | None:
    """Decide app vs website for a known target.

    `find_app(key)` says whether that installed-apps index key exists.
    Returns None when the utterance is not about a known target, or when
    the target can be neither launched nor opened -- the LLM handles it.
    """
    parsed = parse_open_command(text, targets)
    if parsed is None:
        return None
    target, mode = parsed
    app_key = next((k for k in target.apps if find_app(k)), None)

    if mode == "web":
        if target.url:
            return OpenPlan(target, "url", target.url, mode)
        if app_key:
            return OpenPlan(target, "app", app_key, mode)
        return None
    # "auto" and "app": app first, website as the fallback.
    if app_key:
        return OpenPlan(target, "app", app_key, mode)
    if target.url:
        return OpenPlan(target, "url", target.url, mode)
    return None
