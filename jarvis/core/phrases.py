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
from datetime import datetime
from typing import Literal

from jarvis.core.request_context import current_user_transcription

Lang = Literal["ru", "uk", "en"]
LANGS: tuple[Lang, ...] = ("ru", "uk", "en")

# No request text -> English, i.e. the behaviour from before this module.
DEFAULT_LANG: Lang = "en"

_CYRILLIC = re.compile(r"[а-яёіїєґ]", re.IGNORECASE)
_LATIN = re.compile(r"[a-z]", re.IGNORECASE)
_UK_LETTERS = re.compile(r"[іїєґ]", re.IGNORECASE)
# Ukrainian words written without і/ї/є/ґ that Russian does not use. Short
# commands often have none of those letters ("наступний трек", "закрий
# блокнот", "постав таймер на п'ять хвилин"), so the everyday command words
# are listed too; each is spelled differently in Russian.
_UK_WORDS = frozenset({
    "що", "щось", "ще", "це", "як", "дуже", "й", "шо",
    "яка", "який", "яке", "яку", "котра", "котрий", "хто", "чому", "коли",
    "чи", "або", "але", "ось", "його", "буде", "треба", "можна", "зараз",
    "тепер", "трохи", "добре", "гаразд", "дякую", "ласка",
    "наступний", "наступна", "наступне", "наступну",
    "постав", "поставте", "зроби", "зробити", "згорни", "закрий", "закрийте",
    "вимкни", "увімкни", "ввімкни", "зупини", "продовжи", "продовжуй", "грай",
    "нагадай", "нагадайте", "знайди", "шукай", "вийди", "почни", "розпочни",
    "хвилин", "хвилину", "хвилини", "годину",
})
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
    # Task 8b-2: spoken when open_app found no exact match but a close one.
    # {suggestion} is the installed app's display name (Latin, as indexed).
    "did_you_mean_app": {
        "en": "I couldn't find that app, sir. Did you mean {suggestion}?",
        "ru": "Не нашёл такое приложение, сэр. Возможно, вы имели в виду {suggestion}?",
        "uk": "Не знайшов такий застосунок, сер. Можливо, ви мали на увазі {suggestion}?",
    },
    # Task 8b-3: confirmation after learning a personal app alias by voice.
    "alias_learned": {
        "en": "Got it, sir — {alias} now opens {target}.",
        "ru": "Запомнил, сэр — {alias} теперь открывает {target}.",
        "uk": "Запам'ятав, сер — {alias} тепер відкриває {target}.",
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
    # T3.4: Thinking phrases — spoken immediately after STT before LLM.
    # Multiple variants per category; the pipeline picks one at random.
    "thinking_general_1": {
        "en": "One moment, sir.",
        "ru": "Одну секунду, сэр.",
        "uk": "Одну секунду, сер.",
    },
    "thinking_general_2": {
        "en": "Let me think about that.",
        "ru": "Сейчас подумаю.",
        "uk": "Зараз подумаю.",
    },
    "thinking_general_3": {
        "en": "Working on it, sir.",
        "ru": "Работаю над этим, сэр.",
        "uk": "Працюю над цим, сер.",
    },
    "thinking_check_1": {
        "en": "Let me check, sir.",
        "ru": "Сейчас проверю, сэр.",
        "uk": "Зараз перевірю, сер.",
    },
    "thinking_check_2": {
        "en": "Checking now.",
        "ru": "Проверяю.",
        "uk": "Перевіряю.",
    },
    "thinking_long_1": {
        "en": "This might take a moment, sir.",
        "ru": "Это может занять пару секунд, сэр.",
        "uk": "Це може зайняти кілька секунд, сер.",
    },
    # T3.4: Timeout error phrases.
    "error_stt_timeout": {
        "en": "Speech recognition took too long, sir. Could you try again?",
        "ru": "Распознавание речи заняло слишком много времени, сэр. Попробуйте ещё раз.",
        "uk": "Розпізнавання мовлення зайняло занадто багато часу, сер. Спробуйте ще раз.",
    },
    "error_llm_timeout": {
        "en": "The language model is taking too long, sir. Let me try again.",
        "ru": "Языковая модель слишком долго отвечает, сэр. Попробую ещё раз.",
        "uk": "Мовна модель надто довго відповідає, сер. Спробую ще раз.",
    },
    # First-run voice greeting — spoken once on very first launch.
    "first_run_greeting": {
        "en": "Hello, sir! I'm JARVIS, your voice assistant. "
              "Say 'help' to see what I can do.",
        "ru": "Привет, сэр! Я JARVIS, ваш голосовой ассистент. "
              "Скажите 'помощь' чтобы узнать мои возможности.",
        "uk": "Привіт, сер! Я JARVIS, ваш голосовий асистент. "
              "Скажіть 'допомога' щоб дізнатись мої можливості.",
    },
    # ── TTS action / status phrases ──────────────────────────────────
    "timer_expired": {
        "en": "Timer is up, sir.",
        "ru": "Таймер истёк, сэр.",
        "uk": "Таймер спрацював, сер.",
    },
    "timer_done": {
        "en": "Timer '{name}' is done, sir!",
        "ru": "Таймер «{name}» истёк, сэр!",
        "uk": "Таймер «{name}» спрацював, сер!",
    },
    "reminder_fired": {
        "en": "Reminder, sir: {text}",
        "ru": "Напоминаю, сэр: {text}",
        "uk": "Нагадую, сер: {text}",
    },
    "repeat_no_history": {
        "en": "I haven't said anything yet, sir.",
        "ru": "Я ещё ничего не говорил, сэр.",
        "uk": "Я ще нічого не казав, сер.",
    },
    "goodbye": {
        "en": "Goodbye, sir. See you later.",
        "ru": "До свидания, сэр. До встречи.",
        "uk": "До побачення, сер. До зустрічі.",
    },
    "good_morning": {
        "en": "Good morning, sir!",
        "ru": "Доброе утро, сэр!",
        "uk": "Доброго ранку, сер!",
    },
    "good_afternoon": {
        "en": "Good afternoon, sir!",
        "ru": "Добрый день, сэр!",
        "uk": "Доброго дня, сер!",
    },
    "good_evening": {
        "en": "Good evening, sir!",
        "ru": "Добрый вечер, сэр!",
        "uk": "Доброго вечора, сер!",
    },
    "screenshot_saved": {
        "en": "Screenshot saved, sir.",
        "ru": "Скриншот сохранён, сэр.",
        "uk": "Скриншот збережено, сер.",
    },
    # ── Spoken results of voice-routed tools (ToolResult.spoken) ──────
    "volume_up": {"en": "Volume up, sir.", "ru": "Громче, сэр.", "uk": "Гучніше, сер."},
    "volume_down": {"en": "Volume down, sir.", "ru": "Тише, сэр.", "uk": "Тихіше, сер."},
    "volume_mute": {
        "en": "Toggled mute, sir.",
        "ru": "Переключил звук, сэр.",
        "uk": "Перемкнув звук, сер.",
    },
    "locking": {
        "en": "Locking the workstation, sir.",
        "ru": "Блокирую компьютер, сэр.",
        "uk": "Блокую комп'ютер, сер.",
    },
    "closing": {
        "en": "Closing {name}, sir.",
        "ru": "Закрываю {name}, сэр.",
        "uk": "Закриваю {name}, сер.",
    },
    "closing_unnamed": {"en": "Closing it, sir.", "ru": "Закрываю, сэр.", "uk": "Закриваю, сер."},
    "timer_set": {
        "en": "Timer set for {duration}, sir.",
        "ru": "Таймер на {duration}, сэр.",
        "uk": "Таймер на {duration}, сер.",
    },
    "timer_set_named": {
        "en": "Timer '{name}' set for {duration}, sir.",
        "ru": "Таймер «{name}» на {duration}, сэр.",
        "uk": "Таймер «{name}» на {duration}, сер.",
    },
    "timer_cancelled": {
        "en": "Timer '{name}' cancelled, sir.",
        "ru": "Таймер «{name}» отменён, сэр.",
        "uk": "Таймер «{name}» скасовано, сер.",
    },
    "timers_none": {
        "en": "No active timers, sir.",
        "ru": "Активных таймеров нет, сэр.",
        "uk": "Активних таймерів немає, сер.",
    },
    "timers_list": {
        "en": "Active timers: {items}.",
        "ru": "Активные таймеры: {items}.",
        "uk": "Активні таймери: {items}.",
    },
    "timer_left": {
        "en": "{name}, {left} left",
        "ru": "«{name}» ещё {left}",
        "uk": "«{name}» ще {left}",
    },
    "reminder_set_in": {
        "en": "I'll remind you in {duration}, sir: {text}.",
        "ru": "Напомню через {duration}, сэр: {text}.",
        "uk": "Нагадаю через {duration}, сер: {text}.",
    },
    "reminder_set_at": {
        "en": "I'll remind you {when}, sir: {text}.",
        "ru": "Напомню {when}, сэр: {text}.",
        "uk": "Нагадаю {when}, сер: {text}.",
    },
    "reminder_recurring": {
        "en": "I'll remind you {how_often} at {time}, sir: {text}.",
        "ru": "Буду напоминать {how_often} в {time}, сэр: {text}.",
        "uk": "Нагадуватиму {how_often} о {time}, сер: {text}.",
    },
    "reminders_none": {
        "en": "No pending reminders, sir.",
        "ru": "Напоминаний нет, сэр.",
        "uk": "Нагадувань немає, сер.",
    },
    "reminders_list": {
        "en": "Reminders: {items}.",
        "ru": "Напоминания: {items}.",
        "uk": "Нагадування: {items}.",
    },
    "reminder_item": {"en": "{when}, {text}", "ru": "{when} — {text}", "uk": "{when} — {text}"},
    "reminder_deleted": {
        "en": "Deleted the reminder '{text}', sir.",
        "ru": "Удалил напоминание «{text}», сэр.",
        "uk": "Видалив нагадування «{text}», сер.",
    },
    "reminder_snoozed_for": {
        "en": "Snoozed '{text}' for {duration}, sir.",
        "ru": "Отложил «{text}» на {duration}, сэр.",
        "uk": "Відклав «{text}» на {duration}, сер.",
    },
    "reminder_snoozed_until": {
        "en": "Snoozed '{text}' until {time}, sir.",
        "ru": "Отложил «{text}» до {time}, сэр.",
        "uk": "Відклав «{text}» до {time}, сер.",
    },
    "reminder_updated": {
        "en": "Updated the reminder '{text}', sir.",
        "ru": "Изменил напоминание «{text}», сэр.",
        "uk": "Змінив нагадування «{text}», сер.",
    },
    # ── Notes (jarvis/tools/local/notes_tools.py) ────────────────────
    "note_saved": {
        "en": "Note saved, sir. Titled '{title}'.",
        "ru": "Записал, сэр: «{title}».",
        "uk": "Записав, сер: «{title}».",
    },
    "note_appended": {
        "en": "Added to '{title}', sir.",
        "ru": "Добавил в заметку «{title}», сэр.",
        "uk": "Додав до нотатки «{title}», сер.",
    },
    "notes_opening": {
        "en": "Opening your notes, sir.",
        "ru": "Открываю заметки, сэр.",
        "uk": "Відкриваю нотатки, сер.",
    },
    "notes_closed": {
        "en": "Notes closed, sir.",
        "ru": "Закрыл заметки, сэр.",
        "uk": "Закрив нотатки, сер.",
    },
    "note_deleted": {
        "en": "Deleted note '{title}', sir.",
        "ru": "Удалил заметку «{title}», сэр.",
        "uk": "Видалив нотатку «{title}», сер.",
    },
    # ── Media keys (jarvis/tools/local/media_control.py) ─────────────
    "media_pause": {
        "en": "Paused, sir.",
        "ru": "Пауза, сэр.",
        "uk": "Пауза, сер.",
    },
    "media_play": {
        "en": "Playing, sir.",
        "ru": "Продолжаю, сэр.",
        "uk": "Продовжую, сер.",
    },
    "media_toggle": {
        "en": "Toggled playback, sir.",
        "ru": "Готово, сэр.",
        "uk": "Готово, сер.",
    },
    "media_next_track": {
        "en": "Next track, sir.",
        "ru": "Следующий трек, сэр.",
        "uk": "Наступний трек, сер.",
    },
    "media_prev_track": {
        "en": "Previous track, sir.",
        "ru": "Предыдущий трек, сэр.",
        "uk": "Попередній трек, сер.",
    },
    "media_fullscreen": {
        "en": "Fullscreen mode, sir.",
        "ru": "Полный экран, сэр.",
        "uk": "Повний екран, сер.",
    },
    "media_exit_fullscreen": {
        "en": "Exited fullscreen, sir.",
        "ru": "Вышел из полного экрана, сэр.",
        "uk": "Вийшов з повного екрана, сер.",
    },
    "media_skip_forward": {
        "en": "Skipped forward, sir.",
        "ru": "Перемотал вперёд, сэр.",
        "uk": "Перемотав уперед, сер.",
    },
    "media_skip_backward": {
        "en": "Skipped backward, sir.",
        "ru": "Перемотал назад, сэр.",
        "uk": "Перемотав назад, сер.",
    },
    # ── Windows (jarvis/tools/local/window_manager.py) ────────────────────
    "window_focus": {
        "en": "Focused {title}, sir.",
        "ru": "Переключил на {title}, сэр.",
        "uk": "Переключив на {title}, сер.",
    },
    "window_minimize": {
        "en": "Minimized {title}, sir.",
        "ru": "Свернул {title}, сэр.",
        "uk": "Згорнув {title}, сер.",
    },
    "window_maximize": {
        "en": "Maximized {title}, sir.",
        "ru": "Развернул {title}, сэр.",
        "uk": "Розгорнув {title}, сер.",
    },
    "window_restore": {
        "en": "Restored {title}, sir.",
        "ru": "Вернул {title}, сэр.",
        "uk": "Повернув {title}, сер.",
    },
    "window_snap_left": {
        "en": "Snapped {title} left, sir.",
        "ru": "{title} слева, сэр.",
        "uk": "{title} ліворуч, сер.",
    },
    "window_snap_right": {
        "en": "Snapped {title} right, sir.",
        "ru": "{title} справа, сэр.",
        "uk": "{title} праворуч, сер.",
    },
    "window_snap_top_left": {
        "en": "Snapped {title} to the top left, sir.",
        "ru": "{title} слева вверху, сэр.",
        "uk": "{title} ліворуч угорі, сер.",
    },
    "window_snap_top_right": {
        "en": "Snapped {title} to the top right, sir.",
        "ru": "{title} справа вверху, сэр.",
        "uk": "{title} праворуч угорі, сер.",
    },
    "window_snap_bottom_left": {
        "en": "Snapped {title} to the bottom left, sir.",
        "ru": "{title} слева внизу, сэр.",
        "uk": "{title} ліворуч унизу, сер.",
    },
    "window_snap_bottom_right": {
        "en": "Snapped {title} to the bottom right, sir.",
        "ru": "{title} справа внизу, сэр.",
        "uk": "{title} праворуч унизу, сер.",
    },
    "window_snap_center": {
        "en": "Centered {title}, sir.",
        "ru": "{title} по центру, сэр.",
        "uk": "{title} по центру, сер.",
    },
    "window_close": {
        "en": "Closed {title}, sir.",
        "ru": "Закрыл {title}, сэр.",
        "uk": "Закрив {title}, сер.",
    },
    "windows_arranged": {
        "en": "{left} on the left, {right} on the right, sir.",
        "ru": "{left} слева, {right} справа, сэр.",
        "uk": "{left} ліворуч, {right} праворуч, сер.",
    },
    "windows_minimized_all": {
        "en": "Minimized all windows, sir.",
        "ru": "Свернул все окна, сэр.",
        "uk": "Згорнув усі вікна, сер.",
    },
    "windows_restored_all": {
        "en": "Restored all windows, sir.",
        "ru": "Вернул все окна, сэр.",
        "uk": "Повернув усі вікна, сер.",
    },
    "desktop_next": {
        "en": "Switched to the next desktop, sir.",
        "ru": "Следующий рабочий стол, сэр.",
        "uk": "Наступний робочий стіл, сер.",
    },
    "desktop_previous": {
        "en": "Switched to the previous desktop, sir.",
        "ru": "Предыдущий рабочий стол, сэр.",
        "uk": "Попередній робочий стіл, сер.",
    },
    "desktop_task_view": {
        "en": "Opened Task View, sir.",
        "ru": "Открыл все рабочие столы, сэр.",
        "uk": "Відкрив усі робочі столи, сер.",
    },
    "windows_none": {
        "en": "No visible windows found, sir.",
        "ru": "Открытых окон нет, сэр.",
        "uk": "Відкритих вікон немає, сер.",
    },
    "windows_list": {
        "en": "{count} windows open, sir: {titles}.",
        "ru": "Открыто окон: {count}, сэр: {titles}.",
        "uk": "Відкрито вікон: {count}, сер: {titles}.",
    },
    # ── System: clipboard, display, focus, power, network, disk ───────────
    "clipboard_empty": {
        "en": "The clipboard is empty, sir.",
        "ru": "Буфер обмена пуст, сэр.",
        "uk": "Буфер обміну порожній, сер.",
    },
    "clipboard_long": {
        "en": "The clipboard has {count}. It begins: {preview}",
        "ru": "В буфере обмена {count}. Начало: {preview}",
        "uk": "У буфері обміну {count}. Початок: {preview}",
    },
    "brightness_set": {
        "en": "Brightness {level}, sir.",
        "ru": "Яркость {level}, сэр.",
        "uk": "Яскравість {level}, сер.",
    },
    "night_mode_on": {
        "en": "Night light on, sir.",
        "ru": "Ночной режим включён, сэр.",
        "uk": "Нічний режим увімкнено, сер.",
    },
    "night_mode_off": {
        "en": "Night light off, sir.",
        "ru": "Ночной режим выключен, сэр.",
        "uk": "Нічний режим вимкнено, сер.",
    },
    "focus_on": {
        "en": "Focus mode on for {duration}, sir. Distracting sites are blocked.",
        "ru": "Режим фокуса на {duration}, сэр. Отвлекающие сайты заблокированы.",
        "uk": "Режим фокусу на {duration}, сер. Сайти, що відволікають, заблоковано.",
    },
    "focus_off": {
        "en": "Focus mode off, sir. All sites are unblocked.",
        "ru": "Режим фокуса выключен, сэр. Сайты снова доступны.",
        "uk": "Режим фокусу вимкнено, сер. Сайти знову доступні.",
    },
    "focus_inactive": {
        "en": "Focus mode is not on, sir.",
        "ru": "Режим фокуса не включён, сэр.",
        "uk": "Режим фокусу не ввімкнено, сер.",
    },
    "focus_ended": {
        "en": "Focus mode has ended, sir. Sites are unblocked.",
        "ru": "Режим фокуса закончился, сэр. Сайты снова доступны.",
        "uk": "Режим фокусу закінчився, сер. Сайти знову доступні.",
    },
    "focus_active": {
        "en": "Focus mode is on for another {duration}, sir.",
        "ru": "Режим фокуса включён ещё на {duration}, сэр.",
        "uk": "Режим фокусу ввімкнено ще на {duration}, сер.",
    },
    "power_shutdown": {
        "en": "Shutting down in {duration}, sir. Say 'cancel shutdown' to stop it.",
        "ru": (
            "Выключу компьютер через {duration}, сэр. "
            "Чтобы отменить, скажите «отмени выключение»."
        ),
        "uk": "Вимкну комп'ютер через {duration}, сер. Щоб скасувати, скажіть «скасуй вимкнення».",
    },
    "power_restart": {
        "en": "Restarting in {duration}, sir. Say 'cancel restart' to stop it.",
        "ru": (
            "Перезагружу компьютер через {duration}, сэр. "
            "Чтобы отменить, скажите «отмени перезагрузку»."
        ),
        "uk": (
            "Перезавантажу комп'ютер через {duration}, сер. "
            "Щоб скасувати, скажіть «скасуй перезавантаження»."
        ),
    },
    "power_cancelled": {
        "en": "Shutdown cancelled, sir.",
        "ru": "Отменил выключение, сэр.",
        "uk": "Скасував вимкнення, сер.",
    },
    "power_sleep": {
        "en": "Going to sleep, sir.",
        "ru": "Перевожу компьютер в сон, сэр.",
        "uk": "Переводжу комп'ютер у сон, сер.",
    },
    "power_hibernate": {
        "en": "Hibernating, sir.",
        "ru": "Перевожу компьютер в гибернацию, сэр.",
        "uk": "Переводжу комп'ютер у глибокий сон, сер.",
    },
    "power_caffeinate": {
        "en": "The computer will stay awake for {duration}, sir.",
        "ru": "Не дам компьютеру уснуть ещё {duration}, сэр.",
        "uk": "Не дам комп'ютеру заснути ще {duration}, сер.",
    },
    "ip_both": {
        "en": "Your local IP is {local}, the public one is {public}, sir.",
        "ru": "Локальный IP {local}, внешний {public}, сэр.",
        "uk": "Локальна IP {local}, зовнішня {public}, сер.",
    },
    "ip_local": {
        "en": "Your local IP is {local}, sir. The public one is unknown.",
        "ru": "Локальный IP {local}, сэр. Внешний узнать не удалось.",
        "uk": "Локальна IP {local}, сер. Зовнішню дізнатися не вдалося.",
    },
    "ip_public": {
        "en": "Your public IP is {public}, sir.",
        "ru": "Внешний IP {public}, сэр.",
        "uk": "Зовнішня IP {public}, сер.",
    },
    "ip_unknown": {
        "en": "I could not find out your IP address, sir.",
        "ru": "Не удалось узнать IP-адрес, сэр.",
        "uk": "Не вдалося дізнатися IP-адресу, сер.",
    },
    "speed_result": {
        "en": "Download speed is {speed} a second, sir.",
        "ru": "Скорость загрузки {speed} в секунду, сэр.",
        "uk": "Швидкість завантаження {speed} за секунду, сер.",
    },
    "disk_none": {
        "en": "No disk information available, sir.",
        "ru": "Не удалось узнать, сколько места на дисках, сэр.",
        "uk": "Не вдалося дізнатися, скільки місця на дисках, сер.",
    },
    "disk_free": {
        "en": "Free space, sir: {disks}.",
        "ru": "Свободно, сэр: {disks}.",
        "uk": "Вільно, сер: {disks}.",
    },
    "disk_item": {
        "en": "{free} of {total} on {drive}",
        "ru": "на диске {drive} {free} из {total}",
        "uk": "на диску {drive} {free} з {total}",
    },
    "temp_cleaned": {
        "en": "Cleaned {files}, freed {size}, sir.",
        "ru": "Удалил {files}, освободил {size}, сэр.",
        "uk": "Видалив {files}, звільнив {size}, сер.",
    },
    "temp_cleaned_busy": {
        "en": "Cleaned {files}, freed {size}, sir. Files in use were left: {busy}.",
        "ru": "Удалил {files}, освободил {size}, сэр. Занятые файлы оставил: {busy}.",
        "uk": "Видалив {files}, звільнив {size}, сер. Зайняті файли залишив: {busy}.",
    },
    # ── Panels, dictation, notifications ──────────────────────────────────
    "logs_showing": {
        "en": "Showing the logs, sir.",
        "ru": "Показываю логи, сэр.",
        "uk": "Показую логи, сер.",
    },
    "logs_closed": {
        "en": "Logs closed, sir.",
        "ru": "Закрыл логи, сэр.",
        "uk": "Закрив логи, сер.",
    },
    "dashboard_showing": {
        "en": "Showing your dashboard, sir.",
        "ru": "Показываю панель, сэр.",
        "uk": "Показую панель, сер.",
    },
    "dashboard_closed": {
        "en": "Dashboard closed, sir.",
        "ru": "Закрыл панель, сэр.",
        "uk": "Закрив панель, сер.",
    },
    "chat_open": {
        "en": "The chat is open, sir.",
        "ru": "Открыл чат, сэр.",
        "uk": "Відкрив чат, сер.",
    },
    "chat_closed": {
        "en": "Chat closed, sir.",
        "ru": "Закрыл чат, сэр.",
        "uk": "Закрив чат, сер.",
    },
    "clip_history_showing": {
        "en": "Here's your clipboard history, sir.",
        "ru": "Вот история буфера обмена, сэр.",
        "uk": "Ось історія буфера обміну, сер.",
    },
    "clip_history_closed": {
        "en": "Clipboard history closed, sir.",
        "ru": "Закрыл историю буфера, сэр.",
        "uk": "Закрив історію буфера, сер.",
    },
    "clip_history_item": {
        "en": "Item {index} is ready, sir. Press Control V to paste it.",
        "ru": "Запись {index} готова, сэр. Вставьте её через Ctrl+V.",
        "uk": "Запис {index} готовий, сер. Вставте його через Ctrl+V.",
    },
    "clip_history_cleared": {
        "en": "Cleared {items} from the clipboard history, sir.",
        "ru": "Удалил из истории буфера: {items}, сэр.",
        "uk": "Видалив з історії буфера: {items}, сер.",
    },
    "ultra_on": {
        "en": "Deep research Ultra is on, sir.",
        "ru": "Режим «Ультра» включён, сэр.",
        "uk": "Режим «Ультра» увімкнено, сер.",
    },
    "ultra_off": {
        "en": "Deep research Ultra is off, sir.",
        "ru": "Режим «Ультра» выключен, сэр.",
        "uk": "Режим «Ультра» вимкнено, сер.",
    },
    "dictation_already": {
        "en": "Dictation is already on, sir.",
        "ru": "Диктовка уже включена, сэр.",
        "uk": "Диктування вже ввімкнено, сер.",
    },
    "dictation_on": {
        "en": (
            "Dictation on, sir. Everything you say will be typed. Say 'stop dictation' to finish."
        ),
        "ru": (
            "Диктовка включена, сэр. Всё, что скажете, будет напечатано. "
            "Чтобы закончить, скажите «закончи диктовку»."
        ),
        "uk": (
            "Диктування ввімкнено, сер. Усе, що скажете, буде надруковано. "
            "Щоб закінчити, скажіть «заверши диктовку»."
        ),
    },
    "dictation_not_on": {
        "en": "Dictation is not on, sir.",
        "ru": "Диктовка не включена, сэр.",
        "uk": "Диктування не ввімкнено, сер.",
    },
    "dictation_off": {
        "en": "Dictation off, sir.",
        "ru": "Диктовка выключена, сэр.",
        "uk": "Диктування вимкнено, сер.",
    },
    "notification_shown": {
        "en": "Notification shown, sir.",
        "ru": "Показал уведомление, сэр.",
        "uk": "Показав сповіщення, сер.",
    },
    "notification_scheduled": {
        "en": "I'll show the notification in {duration}, sir.",
        "ru": "Покажу уведомление через {duration}, сэр.",
        "uk": "Покажу сповіщення через {duration}, сер.",
    },
    # ── Broken modules (M2-3, jarvis/audio/pipeline.py) ──────────────
    "error_stt_failed": {
        "en": "Speech recognition failed, sir. Please try again.",
        "ru": "Распознавание речи не сработало, сэр. Попробуйте ещё раз.",
        "uk": "Розпізнавання мовлення не спрацювало, сер. Спробуйте ще раз.",
    },
    "module_down_wake_word": {
        "en": "Sir, wake word detection keeps failing, so I switched it off. "
              "Use the push-to-talk hotkey until I am restarted.",
        "ru": "Сэр, распознавание слова «Джарвис» постоянно сбоит, я его отключил. "
              "Пока меня не перезапустят, пользуйтесь горячей клавишей.",
        "uk": "Сер, розпізнавання слова «Джарвіс» постійно збоїть, я його вимкнув. "
              "Поки мене не перезапустять, користуйтеся гарячою клавішею.",
    },
    "module_down_vad": {
        "en": "Sir, detecting the end of your speech keeps failing, "
              "so I may not hear you out. Please restart me.",
        "ru": "Сэр, определение конца фразы постоянно сбоит, "
              "я могу вас не дослушать. Перезапустите меня, пожалуйста.",
        "uk": "Сер, визначення кінця фрази постійно збоїть, "
              "я можу вас не дослухати. Перезапустіть мене, будь ласка.",
    },
    "module_down_stt": {
        "en": "Sir, speech recognition keeps failing. Please restart me.",
        "ru": "Сэр, распознавание речи постоянно сбоит. Перезапустите меня, пожалуйста.",
        "uk": "Сер, розпізнавання мовлення постійно збоїть. Перезапустіть мене, будь ласка.",
    },
    # ── Weather (jarvis/tools/local/weather.py) ──────────────────────
    # {temp} is spelled out with its scale by weather._spoken_temperature.
    "weather_now": {
        "en": "It's {temp} and {conditions}, sir.",
        "ru": "Сейчас {temp}, {conditions}, сэр.",
        "uk": "Зараз {temp}, {conditions}, сер.",
    },
    "weather_now_in": {
        "en": "It's {temp} and {conditions} in {city}, sir.",
        "ru": "{city}: сейчас {temp}, {conditions}, сэр.",
        "uk": "{city}: зараз {temp}, {conditions}, сер.",
    },
    "weather_city_not_found": {
        "en": "I couldn't find {location}. Reporting weather for the default location, sir.",
        "ru": "Не нашёл «{location}». Погода для места по умолчанию, сэр.",
        "uk": "Не знайшов «{location}». Погода для місця за замовчуванням, сер.",
    },
    "weather_no_network": {
        "en": "I can't check the weather right now, sir. No internet connection.",
        "ru": "Не могу узнать погоду, сэр: нет подключения к интернету.",
        "uk": "Не можу дізнатися погоду, сер: немає підключення до інтернету.",
    },
    "weather_unreachable": {
        "en": "I couldn't reach the weather service, sir.",
        "ru": "Не удалось связаться с сервисом погоды, сэр.",
        "uk": "Не вдалося зв'язатися із сервісом погоди, сер.",
    },
}


def say(key: str, lang: Lang | None = None, **values: object) -> str:
    """Phrase `key` in `lang` (default: the request's language)."""
    lang = lang or reply_language()
    variants = PHRASES[key]
    template = variants.get(lang) or variants[DEFAULT_LANG]
    return template.format(**values)


# A timer set without a name gets "timer_<n>" (TimerStore.set_timer);
# that is not a name to read out.
_AUTO_TIMER_NAME = re.compile(r"timer_\d+")


def timer_done_phrase(name: str) -> str:
    """What to say when timer `name` runs out.

    Language: the request that set the timer -- TimerStore runs the
    countdown as an asyncio task, which keeps a copy of that request's
    context -- or else the timer's name ("Одна минута" -> ru)."""
    lang = (
        detect_language(current_user_transcription.get())
        or detect_language(name)
        or DEFAULT_LANG
    )
    if not name or _AUTO_TIMER_NAME.fullmatch(name):
        return say("timer_expired", lang)
    return say("timer_done", lang, name=name)


def reminder_phrase(text: str) -> str:
    """What to say when a reminder fires.

    A reminder can fire days later, long after the request that set it, so
    the language is that of its text (the user's own words)."""
    return say("reminder_fired", detect_language(text) or reply_language(), text=text)


def plural(n: int, one: str, few: str, many: str, lang: Lang) -> str:
    """The form of a noun after the number n: ru / uk rules (1 минуту,
    2 минуты, 5 минут, 21 минуту); English uses one / many."""
    n = abs(n)
    if lang == "en":
        return one if n == 1 else many
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


# Nouns read after a number: (one, few, many) per language.
_UNITS: dict[str, dict[Lang, tuple[str, str, str]]] = {
    "percent": {"ru": ("процент", "процента", "процентов"),
                "uk": ("відсоток", "відсотки", "відсотків"),
                "en": ("percent", "percent", "percent")},
    "gigabyte": {"ru": ("гигабайт", "гигабайта", "гигабайт"),
                 "uk": ("гігабайт", "гігабайти", "гігабайт"),
                 "en": ("gigabyte", "gigabytes", "gigabytes")},
    "megabyte": {"ru": ("мегабайт", "мегабайта", "мегабайт"),
                 "uk": ("мегабайт", "мегабайти", "мегабайт"),
                 "en": ("megabyte", "megabytes", "megabytes")},
    "megabit": {"ru": ("мегабит", "мегабита", "мегабит"),
                "uk": ("мегабіт", "мегабіти", "мегабіт"),
                "en": ("megabit", "megabits", "megabits")},
    "file": {"ru": ("файл", "файла", "файлов"),
             "uk": ("файл", "файли", "файлів"),
             "en": ("file", "files", "files")},
    "item": {"ru": ("запись", "записи", "записей"),
             "uk": ("запис", "записи", "записів"),
             "en": ("item", "items", "items")},
    "character": {"ru": ("символ", "символа", "символов"),
                  "uk": ("символ", "символи", "символів"),
                  "en": ("character", "characters", "characters")},
}


def quantity(n: float, unit: str, lang: Lang | None = None) -> str:
    """'50 процентов', '1 гигабайт', '3 файли', '2 characters': the number
    rounded to a whole one (that is how it is said) and the noun after it."""
    lang = lang or reply_language()
    whole = int(round(n))
    return f"{whole} {plural(whole, *_UNITS[unit][lang], lang)}"


# Accusative forms, for "на 5 минут" / "через 1 минуту" / "for 1 minute".
_DURATION_UNITS: dict[Lang, tuple[tuple[str, str, str], ...]] = {
    "ru": (("час", "часа", "часов"), ("минуту", "минуты", "минут"),
           ("секунду", "секунды", "секунд")),
    "uk": (("годину", "години", "годин"), ("хвилину", "хвилини", "хвилин"),
           ("секунду", "секунди", "секунд")),
    "en": (("hour", "hours", "hours"), ("minute", "minutes", "minutes"),
           ("second", "seconds", "seconds")),
}


def spoken_duration(seconds: float, lang: Lang | None = None) -> str:
    """'1 час 30 минут', '5 хвилин', '45 seconds' (accusative in ru / uk)."""
    lang = lang or reply_language()
    total = max(0, int(round(seconds)))
    parts = []
    for value, forms in zip(
        (total // 3600, total % 3600 // 60, total % 60), _DURATION_UNITS[lang], strict=True,
    ):
        if value:
            parts.append(f"{value} {plural(value, *forms, lang)}")
    if not parts:
        return f"0 {plural(0, *_DURATION_UNITS[lang][2], lang)}"
    return " ".join(parts)


_MONTHS_GENITIVE: dict[Lang, tuple[str, ...]] = {
    "ru": ("января", "февраля", "марта", "апреля", "мая", "июня", "июля",
           "августа", "сентября", "октября", "ноября", "декабря"),
    "uk": ("січня", "лютого", "березня", "квітня", "травня", "червня", "липня",
           "серпня", "вересня", "жовтня", "листопада", "грудня"),
    "en": ("January", "February", "March", "April", "May", "June", "July",
           "August", "September", "October", "November", "December"),
}
_AT: dict[Lang, str] = {"ru": "в", "uk": "о", "en": "at"}
_TOMORROW: dict[Lang, str] = {"ru": "завтра", "uk": "завтра", "en": "tomorrow"}


def spoken_when(moment: datetime, now: datetime | None = None, lang: Lang | None = None) -> str:
    """'в 15:00', 'завтра в 9:05', '7 ноября в 15:00' / 'at 15:00',
    'tomorrow at 9:05', 'November 7 at 15:00' (local time of `moment`)."""
    lang = lang or reply_language()
    now = now or datetime.now(tz=moment.tzinfo)
    clock = f"{_AT[lang]} {moment.hour}:{moment.minute:02d}"
    days = (moment.date() - now.date()).days
    if days == 0:
        return clock
    if days == 1:
        return f"{_TOMORROW[lang]} {clock}"
    month = _MONTHS_GENITIVE[lang][moment.month - 1]
    day = f"{month} {moment.day}" if lang == "en" else f"{moment.day} {month}"
    return f"{day} {clock}"


def closing_phrase(name: str, lang: Lang | None = None) -> str:
    """'Closing {name}, sir.' in the request's language."""
    lang = lang or reply_language()
    spoken = spoken_name(name, lang)
    if spoken:
        return say("closing", lang, name=spoken)
    return say("closing_unnamed", lang)


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
