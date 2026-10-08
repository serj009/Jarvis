"""Spoken phrases in the user's language (ru / uk / en).

Tools used to answer in English only ("Opening YouTube, sir."), which the
Russian Piper voice reads as gibberish. Now a tool asks for a phrase by
key and gets it in the language of the request being handled.

The language comes from the text of the current request
(`current_user_transcription`, set by the app around each turn):
- Ukrainian-only letters (і ї є ґ) or Ukrainian-only words -> "uk";
- other Cyrillic -> "ru";
- Latin letters only -> "en".
Outside a turn (tests, UI) there is no request text, so the language is
English and the old phrases stay exactly as they were.

Brand names are read badly by the ru/uk voices when written in Latin
letters, so `spoken_name()` gives a Cyrillic spelling for known names.
An unknown Latin name is dropped from the phrase ("Открываю, сэр.")
rather than mispronounced.

Strings come from the Update Pack (`i18n_action_strings.yaml`,
`i18n_fixes.yaml`). Plain Python on purpose: no YAML loading at runtime.
"""

from __future__ import annotations

import re
from typing import Literal

from jarvis.core.request_context import current_user_transcription

Lang = Literal["ru", "uk", "en"]
LANGS: tuple[Lang, ...] = ("ru", "uk", "en")

# No request text -> English, i.e. the behaviour from before this module.
DEFAULT_LANG: Lang = "en"

_CYRILLIC = re.compile(r"[а-яёіїєґ]", re.IGNORECASE)
_LATIN = re.compile(r"[a-z]", re.IGNORECASE)
_UK_LETTERS = re.compile(r"[іїєґ]", re.IGNORECASE)
# Ukrainian words written without і/ї/є/ґ that Russian does not use.
_UK_WORDS = frozenset({"що", "щось", "ще", "це", "як", "дуже", "й", "шо"})
_WORD = re.compile(r"[а-яёіїєґ']+", re.IGNORECASE)


def detect_language(text: str | None) -> Lang | None:
    """Language of `text`, or None when there are no letters to judge by."""
    if not text:
        return None
    if _CYRILLIC.search(text):
        if _UK_LETTERS.search(text):
            return "uk"
        words = {w.lower() for w in _WORD.findall(text)}
        return "uk" if words & _UK_WORDS else "ru"
    if _LATIN.search(text):
        return "en"
    return None


def reply_language() -> Lang:
    """Language to answer in: the language of the request being handled."""
    return detect_language(current_user_transcription.get()) or DEFAULT_LANG


PHRASES: dict[str, dict[Lang, str]] = {
    "opening": {
        "en": "Opening {name}, sir.",
        "ru": "Открываю {name}, сэр.",
        "uk": "Відкриваю {name}, сер.",
    },
    "opening_unnamed": {
        "en": "Opening it, sir.",
        "ru": "Открываю, сэр.",
        "uk": "Відкриваю, сер.",
    },
    "searching_web": {
        "en": "Searching the web for {query}, sir.",
        "ru": "Ищу в интернете: {query}, сэр.",
        "uk": "Шукаю в інтернеті: {query}, сер.",
    },
    "playing": {
        "en": "Playing {query}, sir.",
        "ru": "Включаю {query}, сэр.",
        "uk": "Вмикаю {query}, сер.",
    },
    "playing_on_youtube": {
        "en": "Playing {query} on YouTube, sir.",
        "ru": "Включаю {query} на Ютубе, сэр.",
        "uk": "Вмикаю {query} на Ютубі, сер.",
    },
    "playing_unnamed": {
        "en": "Playing that on YouTube, sir.",
        "ru": "Включаю на Ютубе, сэр.",
        "uk": "Вмикаю на Ютубі, сер.",
    },
    "done": {
        "en": "Done, sir.",
        "ru": "Готово, сэр.",
        "uk": "Зроблено, сер.",
    },
    # Spoken instead of the raw error text: details stay in the log.
    "generic_fail": {
        "en": "I couldn't do that, sir.",
        "ru": "Боюсь, что-то пошло не так.",
        "uk": "На жаль, щось пішло не так.",
    },
    # T1.5 / T2.4: Error phrases for the ErrorHandler and confidence routing.
    "error_generic": {
        "en": "Something went wrong, sir. I'll try again.",
        "ru": "Что-то пошло не так, сэр. Сейчас попробую ещё раз.",
        "uk": "Щось пішло не так, сер. Зараз спробую ще раз.",
    },
    "error_stt": {
        "en": "I couldn't understand what you said, sir. Could you repeat?",
        "ru": "Не удалось распознать речь, сэр. Повторите, пожалуйста.",
        "uk": "Не вдалося розпізнати мовлення, сер. Повторіть, будь ласка.",
    },
    "error_tts": {
        "en": "Voice synthesis failed, sir. Switching to fallback.",
        "ru": "Синтез голоса не удался, сэр. Переключаюсь на резервный.",
        "uk": "Синтез голосу не вдався, сер. Перемикаюсь на резервний.",
    },
    "error_vram": {
        "en": "Not enough GPU memory, sir. Freeing resources.",
        "ru": "Недостаточно видеопамяти, сэр. Освобождаю ресурсы.",
        "uk": "Недостатньо відеопам'яті, сер. Звільняю ресурси.",
    },
    "error_ollama": {
        "en": "The language model is not responding, sir. I'll retry shortly.",
        "ru": "Языковая модель не отвечает, сэр. Скоро попробую снова.",
        "uk": "Мовна модель не відповідає, сер. Скоро спробую знову.",
    },
    "error_degraded": {
        "en": "Multiple errors in a row, sir. Some features may be limited.",
        "ru": "Несколько ошибок подряд, сэр. Некоторые функции могут быть ограничены.",
        "uk": "Кілька помилок поспіль, сер. Деякі функції можуть бути обмежені.",
    },
}


def say(key: str, lang: Lang | None = None, **values: object) -> str:
    """Phrase `key` in `lang` (default: the request's language)."""
    lang = lang or reply_language()
    variants = PHRASES[key]
    template = variants.get(lang) or variants[DEFAULT_LANG]
    return template.format(**values)


# Cyrillic spellings (ru, uk) for names the ru/uk voices would misread.
# Accusative where it differs ("Открываю Википедию").
# Keys: lowercase English name as tools display it.
_SPOKEN_NAMES: dict[str, tuple[str, str]] = {
    "youtube": ("Ютуб", "Ютуб"),
    "youtube music": ("Ютуб Мьюзик", "Ютуб М'юзік"),
    "github": ("Гитхаб", "Гітхаб"),
    "github desktop": ("Гитхаб", "Гітхаб"),
    "telegram": ("Телеграм", "Телеграм"),
    "telegram desktop": ("Телеграм", "Телеграм"),
    "google": ("Гугл", "Гугл"),
    "gmail": ("Джимейл", "Джимейл"),
    "wikipedia": ("Википедию", "Вікіпедію"),
    "discord": ("Дискорд", "Діскорд"),
    "steam": ("Стим", "Стім"),
    "spotify": ("Спотифай", "Спотіфай"),
    "reddit": ("Реддит", "Реддіт"),
    "twitch": ("Твич", "Твіч"),
    "netflix": ("Нетфликс", "Нетфлікс"),
    "chatgpt": ("Чат Джи Пи Ти", "Чат Джі Пі Ті"),
    "stack overflow": ("Стак Оверфлоу", "Стак Оверфлоу"),
    "amazon": ("Амазон", "Амазон"),
    "twitter": ("Твиттер", "Твіттер"),
    "instagram": ("Инстаграм", "Інстаграм"),
    "chrome": ("Хром", "Хром"),
    "google chrome": ("Гугл Хром", "Гугл Хром"),
    "firefox": ("Файрфокс", "Файрфокс"),
    "mozilla firefox": ("Файрфокс", "Файрфокс"),
    "edge": ("Эдж", "Едж"),
    "microsoft edge": ("Эдж", "Едж"),
    "visual studio code": ("Вижуал Студио Код", "Віжуал Студіо Код"),
    "notepad": ("Блокнот", "Блокнот"),
    "calculator": ("Калькулятор", "Калькулятор"),
    "explorer": ("Проводник", "Провідник"),
    "file explorer": ("Проводник", "Провідник"),
}


def spoken_name(name: str, lang: Lang | None = None) -> str | None:
    """How to say `name` in `lang`; None if it cannot be said well."""
    lang = lang or reply_language()
    name = name.strip()
    if not name or lang == "en":
        return name or None
    known = _SPOKEN_NAMES.get(re.sub(r"\s+", " ", name.lower()))
    if known is not None:
        return known[0] if lang == "ru" else known[1]
    # A Latin-only name the ru/uk voice would garble: leave it out.
    if _LATIN.search(name) and not _CYRILLIC.search(name):
        return None
    return name


def opening_phrase(name: str, lang: Lang | None = None) -> str:
    """'Opening {name}, sir.' in the request's language."""
    lang = lang or reply_language()
    spoken = spoken_name(name, lang)
    if spoken:
        return say("opening", lang, name=spoken)
    return say("opening_unnamed", lang)
