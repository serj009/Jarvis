"""Voice-routing collision tests (Этап 10).

The router matches voice patterns first-match-wins by ascending priority
(see registry.PRIORITY_* and catalogue.voice_pattern_catalogue). A greedy
``(.+)`` pattern on a common verb ("запусти …", "run …") can silently
steal a phrase meant for another tool. The headline example from the audit:
``cli_runner``'s "запусти X" used to grab "запусти хром" (an app) and
"запусти сталкер в стиме" (a Steam game), then fail the command whitelist.

These tests pin the resolution of a set of representative phrases to the
CORRECT tool, so a future greedy pattern that re-introduces the collision
fails CI.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from jarvis.tools.catalogue import voice_pattern_catalogue


def _route(phrase: str) -> str | None:
    """Return the tool name the router would pick for *phrase*.

    Mirrors the router: patterns are tried in ascending priority
    (voice_pattern_catalogue is already priority-sorted) and the first
    regex that matches wins.
    """
    for tool_name, vp in voice_pattern_catalogue():
        if re.match(vp.regex, phrase, re.IGNORECASE):
            return tool_name
    return None


# (phrase, expected tool name). These are the collision cases Этап 10 names
# plus the neighbours they must not steal from.
_CASES = [
    # App launches must go to open_app, NOT cli_runner / steam / code_runner.
    ("запусти хром", "open_app"),
    ("открой блокнот", "open_app"),
    ("запусти калькулятор", "open_app"),
    # Explicit "в стиме" -> launch_steam_game (legacy, Steam-only).
    ("запусти сталкер в стиме", "launch_steam_game"),
    # Task 8c commit 4: generic "игру X" now -> launch_game (all launchers).
    ("запусти игру ведьмак", "launch_game"),
    # Whitelisted shell commands -> run_command (cli_runner).
    ("запусти ping 127.0.0.1", "run_command"),
    ("run dir", "run_command"),
    ("выполни whoami", "run_command"),
    # Test / script execution -> code_runner.
    ("запусти тесты", "code_runner"),
    # A domain with "в браузере" before or after it is a website, not a
    # program: open_app's "открой (.+)" used to try to launch
    # "в браузере uakino.best" (seen in a live session).
    # Everyday volume forms (M3): "сделай громче" went to the autonomous
    # agent's "сделай (.+)", "выключи звук" to close_app ("звук" as an app).
    # Durations in words (M3): "на пять минут", "через полчаса", "через час"
    # went to the model, the patterns understood only digits.
    ("поставь таймер на пять минут", "set_timer"),
    ("поставь таймер на полчаса", "set_timer"),
    ("постав таймер на півхвилини", "set_timer"),
    ("set a timer for twenty minutes", "set_timer"),
    ("напомни через пять минут купить хлеб", "remind_me"),
    ("напомни через час выключить плиту", "remind_me"),
    ("напомни через 30 секунд", "remind_me"),
    ("нагадай через годину подзвонити", "remind_me"),
    ("remind me in an hour to stretch", "remind_me"),
    ("remind me in 30 seconds", "remind_me"),
    ("notify me in half an hour", "schedule_notification"),
    ("громче", "volume"),
    ("сделай тише", "volume"),
    ("выключи звук", "volume"),
    ("зроби гучніше", "volume"),
    ("вимкни звук", "volume"),
    ("louder", "volume"),
    ("сделай бэкап сохранений ведьмак", "backup_game_saves"),
    ("открой uakino.best", "open_url"),
    ("открой в браузере uakino.best", "open_url"),
    ("открой uakino.best в браузере", "open_url"),
    ("відкрий у браузері uakino.best", "open_url"),
    ("open example.com in the browser", "open_url"),
    # "в браузере" with no domain (STT heard "uakino.best" as "ur кино",
    # or a page was named) is a web search, never a program to launch.
    ("открой в браузере ur кино", "open_url"),
    ("открой в браузере «железный человек-фильм»", "open_url"),
    ("открой железный человек в браузере", "open_url"),
    ("открою в браузере uakino.best", "open_url"),
    # "найди / поищи X" is a web search unless X is said to be a file (live:
    # "найди гайд по Spyro" ran a 4 s disk search).
    ("найди гайд по достижениям spyro", "open_url"),
    ("найди в интернете гайд по spyro", "open_url"),
    ("поищи рецепт борща", "open_url"),
    ("знайди рецепт борщу", "open_url"),
    ("найди файл отчет", "file_manager"),
    ("найди документ договор", "file_manager"),
    ("найди отчет.docx", "file_manager"),
    ("найди pdf в загрузках", "file_manager"),
    ("знайди файл звіт", "file_manager"),
    # "Remember the page" with a tail (live: "…в браузере в память" became a
    # note via take_note's "запомни (.+)").
    ("запомни открытую страницу в браузере в память", "save_page_knowledge"),
    ("сохрани страницу из браузера", "save_page_knowledge"),
    # Two adjectives and an STT comma (both seen live, both became notes).
    ("запомни текущую открытую страницу в браузере в память", "save_page_knowledge"),
    ("запомни открытую страницу в браузере, в память", "save_page_knowledge"),
    ("запам'ятай цю сторінку в пам'ять", "save_page_knowledge"),
    ("save the current page to memory", "save_page_knowledge"),
    ("запомни эту страницу в закладки", "bookmarks"),
    ("запомни купить молоко", "take_note"),
    # Neighbours that must keep their own tools.
    ("открой новую вкладку", "browser_tabs"),
    ("открой заметки", "open_notes"),
    # Protocols -> protocol tool (most specific, lowest priority number).
    ("запусти протокол вечеринка", "run_protocol"),
    # "Remember the PAGE" reads the page into memory (save_page_knowledge);
    # take_note's "запомни (.+)" used to steal these and save a note reading
    # "эту страницу" without ever opening the page.
    ("запомни эту страницу", "save_page_knowledge"),
    ("сохрани эту страницу", "save_page_knowledge"),
    ("сохрани этот рецепт", "save_page_knowledge"),
    ("запомни гайд с этой страницы", "save_page_knowledge"),
    ("сохрани это прохождение", "save_page_knowledge"),
    ("запомни что на странице", "save_page_knowledge"),
    ("запам'ятай цю сторінку", "save_page_knowledge"),
    ("запамʼятай цю сторінку", "save_page_knowledge"),
    ("запам’ятай цю сторінку", "save_page_knowledge"),
    ("збережи рецепт", "save_page_knowledge"),
    ("remember this page", "save_page_knowledge"),
    ("save this recipe", "save_page_knowledge"),
    # "Save the LINK" -> bookmarks.
    ("сохрани ссылку", "bookmarks"),
    ("запомни эту ссылку", "bookmarks"),
    ("сохрани страницу в закладки", "bookmarks"),
    ("сохрани в закладки", "bookmarks"),
    ("збережи посилання", "bookmarks"),
    ("save this link", "bookmarks"),
    # Ordinary notes stay notes.
    ("запомни купить молоко", "take_note"),
    ("запомни что завтра встреча в три", "take_note"),
    ("remember this the bins go out tuesday", "take_note"),
    ("запам'ятай купити хліб", "take_note"),
    # Neighbours on the same nouns keep their tools.
    ("прочитай эту страницу", "content_reader"),
    ("сохрани страницу как pdf", "content_reader"),
    ("сохрани картинки со страницы", "scrape_images"),
    # Polite forms and "current / open page".
    ("сохраните текущую страницу", "save_page_knowledge"),
    ("запомните эту страницу", "save_page_knowledge"),
    ("сохрани открытую страницу", "save_page_knowledge"),
    ("збережіть поточну сторінку", "save_page_knowledge"),
    ("save the current page", "save_page_knowledge"),
    ("запомните купить хлеб", "take_note"),
    # Using a saved list.
    ("что осталось", "saved_list_status"),
    ("что ещё осталось в блич", "saved_list_status"),
    ("сколько процентов пройдено", "saved_list_status"),
    ("список продуктов для борща", "saved_list_status"),
    ("пересчитай борщ на 6 порций", "saved_list_status"),
    ("що залишилось", "saved_list_status"),
    ("what's left", "saved_list_status"),
    ("отметь крылья феникса выполненным", "mark_list_item"),
    ("сними отметку с король душ", "mark_list_item"),
    ("познач крила фенікса виконаним", "mark_list_item"),
    ("mark phoenix wings as done", "mark_list_item"),
    ("покажи весь список", "saved_list_status"),
    ("покажи рецепт борща", "saved_list_status"),
    ("show the whole list", "saved_list_status"),
    ("покажи мои закладки", "bookmarks"),
    ("show my bookmarks", "bookmarks"),
    # Naming a saved list must beat file_manager's generic rename...
    ("назови этот список блич", "name_saved_list"),
    ("переименуй список блич в бличик", "name_saved_list"),
    ("переименуй этот список в блич", "name_saved_list"),
    ("rename the list bleach to bl", "name_saved_list"),
    # ... while files are still renamed by file_manager.
    ("переименуй файл а в б", "file_manager"),
    ("переименуй папку фото в отпуск", "file_manager"),
    ("rename report.txt to old.txt", "file_manager"),
    # ... and their neighbours keep their tools.
    ("сколько будет 5 процентов от 200", "calculator"),
    ("список таймеров", "list_timers"),
    ("список протоколов", "list_protocols"),
]


@pytest.mark.parametrize("phrase,expected", _CASES)
def test_phrase_routes_to_expected_tool(phrase: str, expected: str) -> None:
    got = _route(phrase)
    assert got == expected, (
        f"{phrase!r} routed to {got!r}, expected {expected!r}"
    )


@pytest.mark.parametrize(
    "phrase",
    [
        # Questions, not tasks: the autonomous agent's "do (.+)" took them
        # with the goal "you remember my name" (M3 regression probe).
        "do you remember my name",
        "do i have anything today",
        "do we have milk",
    ],
)
def test_questions_are_not_agent_goals(phrase: str) -> None:
    assert _route(phrase) != "autonomous_agent"


@pytest.mark.parametrize(
    "phrase",
    [
        # A bare "удали X" is not "uninstall X" (M3 regression probe): these
        # were offered for uninstalling through winget.
        "удали файл test.txt на рабочем столе",
        "удали из памяти мой адрес",
        "удали заметку покупки",
        "удали закладку ютуб",
        "видали нотатку покупки",
    ],
)
def test_delete_is_not_uninstall(phrase: str) -> None:
    assert _route(phrase) != "package_uninstall"


@pytest.mark.parametrize(
    "phrase",
    ["удали из памяти мой адрес", "удали заметку покупки", "видали нотатку покупки",
     "удали историю буфера"],
)
def test_delete_without_a_file_is_not_a_file(phrase: str) -> None:
    assert _route(phrase) != "file_manager"


@pytest.mark.parametrize(
    "phrase",
    ["удали файл test.txt на рабочем столе", "удали report.docx", "удали папку старое",
     "видали файл test.txt"],
)
def test_delete_a_file(phrase: str) -> None:
    assert _route(phrase) == "file_manager"


def _route_args(phrase: str) -> tuple[str, dict]:
    for tool_name, vp in voice_pattern_catalogue():
        m = re.match(vp.regex, phrase, re.IGNORECASE)
        if m:
            return tool_name, (vp.args(m) if vp.args else {})
    raise AssertionError(f"{phrase!r} matched nothing")


@pytest.mark.parametrize(
    ("phrase", "folder", "name"),
    [
        # The home check for M2-4 says "удали файл test.txt на рабочем столе";
        # the folder stayed in the path ("test.txt на рабочем столе").
        ("удали файл test.txt на рабочем столе", "Desktop", "test.txt"),
        ("видали файл test.txt на робочому столі", "Desktop", "test.txt"),
        ("удали report.docx в загрузках", "Downloads", "report.docx"),
        ("delete test.txt on the desktop", "Desktop", "test.txt"),
    ],
)
def test_delete_a_file_in_a_known_folder(phrase: str, folder: str, name: str) -> None:
    tool, args = _route_args(phrase)
    assert tool == "file_manager"
    assert args == {"action": "delete", "path": str(Path.home() / folder / name)}


@pytest.mark.parametrize(
    "phrase", ["создай файл заметки.md на рабочем столе", "create file notes.md on the desktop"]
)
def test_create_a_file_on_the_desktop(phrase: str) -> None:
    """"на рабочем столе" was looked up as the key "рабочем столе" and dropped."""
    tool, args = _route_args(phrase)
    assert tool == "create_file"
    assert Path(args["path"]).parent == Path.home() / "Desktop"


@pytest.mark.parametrize(
    "phrase", ["удали программу zoom", "видали програму zoom", "uninstall zoom"]
)
def test_uninstall_a_program(phrase: str) -> None:
    assert _route(phrase) == "package_uninstall"


def test_agent_still_takes_tasks() -> None:
    assert _route("do the backups") == "autonomous_agent"


def test_run_command_does_not_steal_app_launches() -> None:
    """cli_runner must never be the match for a non-whitelisted 'запусти X'."""
    for phrase in ("запусти хром", "запусти сталкер в стиме", "запусти музыку"):
        assert _route(phrase) != "run_command", (
            f"cli_runner stole {phrase!r}"
        )


def test_run_command_still_catches_whitelisted() -> None:
    """A whitelisted command after 'run/запусти' still reaches run_command."""
    for phrase in ("run dir", "запусти ping 8.8.8.8", "выполни systeminfo",
                   "run git status", "запусти pip list"):
        assert _route(phrase) == "run_command", (
            f"{phrase!r} did not reach run_command"
        )


@pytest.mark.parametrize(
    ("phrase", "action", "window"),
    [
        # The greedy "(.+)" kept the preposition in the window name: "хром в".
        ("помести хром в верхний левый угол", "snap_top_left", "хром"),
        ("помести хром в левый верхний угол", "snap_top_left", "хром"),
        ("поставь дискорд нижний правый", "snap_bottom_right", "дискорд"),
        ("постав хром у верхній лівий кут", "snap_top_left", "хром"),
        ("snap chrome to the top left", "snap_top_left", "chrome"),
        ("snap vs code top right", "snap_top_right", "vs code"),
        ("snap chrome to the left", "snap_left", "chrome"),
        ("snap chrome left", "snap_left", "chrome"),
    ],
)
def test_snap_window_name_and_corner(phrase: str, action: str, window: str) -> None:
    assert _route_args(phrase) == ("window_action", {"action": action, "window": window})


@pytest.mark.parametrize(
    ("phrase", "query"),
    [
        ("search the web for tide tables", "tide+tables"),
        ("search the internet for cheap flights", "cheap+flights"),
        ("search online for pasta recipes", "pasta+recipes"),
        ("search for puppies", "puppies"),
    ],
)
def test_web_search_query_leaves_out_the_web(phrase: str, query: str) -> None:
    """"search the web for tide tables" searched for "the web for tide tables"."""
    tool, args = _route_args(phrase)
    assert tool == "open_url"
    assert args["url"].endswith(f"?q={query}")


@pytest.mark.parametrize(
    ("phrase", "content"),
    [
        # "запиши" is Russian and Ukrainian; the Russian pattern comes first
        # and kept "нотатку" in the note.
        ("запиши нотатку купити хліб", "купити хліб"),
        ("запиши собі в нотатки подзвонити", "подзвонити"),
        ("запиши заметку купить хлеб", "купить хлеб"),
        # "создай заметку X" went to the model (and before d520629, to
        # image generation).
        ("создай заметку купить хлеб", "купить хлеб"),
        ("створи нотатку купити хліб", "купити хліб"),
    ],
)
def test_note_content_leaves_out_the_command(phrase: str, content: str) -> None:
    assert _route_args(phrase) == ("take_note", {"content": content})


@pytest.mark.parametrize(
    ("phrase", "time", "tomorrow"),
    [
        # Clock times in words went to the model: only "15:00" / "в 9" matched.
        ("напомни в десять позвонить", "10:00", False),
        ("напомни в восемь вечера купить хлеб", "20:00", False),
        ("напомни завтра в девять утра проверить почту", "09:00", True),
        ("нагадай о десятій подзвонити", "10:00", False),
        ("remind me at seven pm to call mom", "19:00", False),
        ("напомни в 15:00 позвонить маме", "15:00", False),
    ],
)
def test_reminder_at_a_spoken_time(phrase: str, time: str, tomorrow: bool) -> None:
    tool, args = _route_args(phrase)
    assert tool == "remind_me_at"
    assert (args["time"], args["tomorrow"]) == (time, tomorrow)
