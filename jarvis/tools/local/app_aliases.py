"""Extended multilingual alias dictionary for open_app / close_app.

Maps informal and spoken application names in Russian (RU), Ukrainian (UK),
and English (EN) to canonical Windows launch targets — executable names,
Start-Menu display names, protocol URIs, or shell namespace paths.

The canonical target is what the existing open_app resolution chain
(fuzzy installed-app match → alias → App Paths / PATH → shell) can handle.

Usage::

    from jarvis.tools.local.app_aliases import APP_ALIASES, resolve_app_alias

    resolved = resolve_app_alias(user_input)   # "хром" → "chrome"
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Alias → Windows launch target
#
# Keys:   lowercase, stripped — the form STT / LLM output produces after
#         normalize_open_query().  Lookup is ``key = name.strip().lower()``.
# Values: one of
#   • bare executable name resolvable via App Paths / PATH  (chrome, vlc)
#   • Start-Menu display name for the fuzzy installed-app matcher  (Tor Browser)
#   • Windows protocol / shell URI  (ms-settings:, shell:RecycleBinFolder)
#   • HTTPS URL for web-only targets  (https://docs.google.com)
#
# 71 unique programs, ~200 alias entries across RU / UK / EN.
# ---------------------------------------------------------------------------

APP_ALIASES: dict[str, str] = {
    # =================================================================
    # Browsers
    # =================================================================
    # -- Google Chrome --
    "хром": "chrome",
    "гугл хром": "chrome",
    "гугл": "chrome",
    "chrome": "chrome",
    "google chrome": "chrome",
    # -- Mozilla Firefox --
    "фаерфокс": "firefox",
    "файрфокс": "firefox",
    "мозилла": "firefox",
    "firefox": "firefox",
    "mozilla": "firefox",
    # -- Microsoft Edge --
    "эдж": "msedge",
    "едж": "msedge",
    "edge": "msedge",
    "microsoft edge": "msedge",
    # -- Opera --
    "опера": "opera",
    "opera": "opera",
    # -- Brave --
    "брейв": "brave",
    "brave": "brave",
    # -- Vivaldi --
    "вивальди": "vivaldi",
    "vivaldi": "vivaldi",
    # -- Tor Browser --
    "тор": "Tor Browser",
    "tor": "Tor Browser",
    "tor browser": "Tor Browser",
    # -- Yandex Browser --
    "яндекс": "yandex",
    "яндекс браузер": "yandex",
    "yandex": "yandex",
    "yandex browser": "yandex",

    # =================================================================
    # Messengers
    # =================================================================
    # -- Telegram --
    "телеграм": "telegram",
    "телеграмм": "telegram",
    "телега": "telegram",
    "telegram": "telegram",
    "тг": "telegram",
    # -- Discord --
    "дискорд": "discord",
    "діскорд": "discord",
    "discord": "discord",
    # -- Viber --
    "вайбер": "viber",
    "viber": "viber",
    # -- WhatsApp --
    "ватсап": "whatsapp",
    "вацап": "whatsapp",
    "whatsapp": "whatsapp",
    "whats app": "whatsapp",
    # -- Skype --
    "скайп": "skype",
    "skype": "skype",
    # -- Slack --
    "слак": "slack",
    "slack": "slack",
    # -- Signal --
    "сигнал": "signal",
    "signal": "signal",
    # -- Microsoft Teams --
    "тимс": "ms-teams",
    "тімс": "ms-teams",
    "teams": "ms-teams",
    "microsoft teams": "ms-teams",
    # -- Zoom --
    "зум": "zoom",
    "zoom": "zoom",

    # =================================================================
    # Office
    # =================================================================
    # -- Microsoft Word --
    "ворд": "winword",
    "word": "winword",
    "microsoft word": "winword",
    # -- Microsoft Excel --
    "эксель": "excel",
    "ексель": "excel",
    "excel": "excel",
    "microsoft excel": "excel",
    # -- Microsoft PowerPoint --
    "повер поинт": "powerpnt",
    "поверпоинт": "powerpnt",
    "powerpoint": "powerpnt",
    "пп": "powerpnt",
    "microsoft powerpoint": "powerpnt",
    # -- Microsoft OneNote --
    "ваннот": "onenote",
    "onenote": "onenote",
    "one note": "onenote",
    # -- Microsoft Outlook --
    "аутлук": "outlook",
    "outlook": "outlook",
    # -- LibreOffice --
    "либреофис": "soffice",
    "libreoffice": "soffice",
    # -- Google Docs (web-only → opens in default browser) --
    "гугл документы": "https://docs.google.com",
    "google docs": "https://docs.google.com",

    # =================================================================
    # Development
    # =================================================================
    # -- Visual Studio Code --
    "вскод": "code",
    "vscode": "code",
    "vs code": "code",
    "код": "code",
    "code": "code",
    "визуал студио код": "code",
    "visual studio code": "code",
    # -- IntelliJ IDEA --
    "интелидж": "idea64",
    "intellij": "idea64",
    "idea": "idea64",
    # -- PyCharm --
    "пайчарм": "pycharm64",
    "pycharm": "pycharm64",
    # -- WebStorm --
    "вебшторм": "webstorm64",
    "webstorm": "webstorm64",
    # -- Sublime Text --
    "саблайм": "subl",
    "sublime": "subl",
    "sublime text": "subl",
    # -- Notepad++ --
    "нотпад плюс": "notepad++",
    "notepad++": "notepad++",
    "notepad plus": "notepad++",
    # -- Git Bash --
    "гит баш": "git-bash",
    "git bash": "git-bash",
    # -- Windows Terminal --
    "терминал": "wt",
    "terminal": "wt",
    "windows terminal": "wt",
    # -- PowerShell --
    "повершелл": "powershell",
    "powershell": "powershell",
    # -- Command Prompt --
    "командная строка": "cmd",
    "cmd": "cmd",
    "command prompt": "cmd",

    # =================================================================
    # Multimedia
    # =================================================================
    # -- Spotify --
    "спотифай": "spotify",
    "spotify": "spotify",
    # -- VLC --
    "влс": "vlc",
    "vlc": "vlc",
    "vlc player": "vlc",
    # -- Netflix --
    "нетфликс": "netflix",
    "netflix": "netflix",
    # -- YouTube Music (web-only → opens in default browser) --
    "ютуб музыка": "https://music.youtube.com",
    "youtube music": "https://music.youtube.com",

    # =================================================================
    # Windows System
    # =================================================================
    # -- File Explorer --
    "проводник": "explorer",
    "провідник": "explorer",
    "explorer": "explorer",
    "file explorer": "explorer",
    "мой компьютер": "explorer",
    "мій комп'ютер": "explorer",
    # -- Task Manager --
    "диспетчер задач": "taskmgr",
    "диспетчер": "taskmgr",
    "task manager": "taskmgr",
    "taskmgr": "taskmgr",
    # -- Control Panel --
    "панель управления": "control",
    "control panel": "control",
    "control": "control",
    # -- Settings --
    "настройки": "ms-settings:",
    "налаштування": "ms-settings:",
    "settings": "ms-settings:",
    "параметры": "ms-settings:",
    # -- Calculator --
    "калькулятор": "calc",
    "calculator": "calc",
    "calc": "calc",
    # -- Notepad --
    "блокнот": "notepad",
    "notepad": "notepad",
    # -- Paint --
    "пейнт": "mspaint",
    "paint": "mspaint",
    "mspaint": "mspaint",
    # -- Snipping Tool --
    "ножницы": "snippingtool",
    "snipping tool": "snippingtool",
    "ножиці": "snippingtool",
    # -- Screen Recording (Xbox Game Bar) --
    "запись экрана": "ms-gamebar:",
    "screen recorder": "ms-gamebar:",
    # -- Camera --
    "камера": "camera",
    "camera": "camera",
    # -- Clock / Alarm --
    "часы": "ms-clock:",
    "clock": "ms-clock:",
    "будильник": "ms-clock:",
    "alarm": "ms-clock:",
    # -- Microsoft Store --
    "магазин": "ms-windows-store:",
    "store": "ms-windows-store:",
    "microsoft store": "ms-windows-store:",
    "магазин додатків": "ms-windows-store:",

    # =================================================================
    # Gaming
    # =================================================================
    # -- Steam --
    "стим": "steam",
    "стім": "steam",
    "steam": "steam",
    # -- Epic Games --
    "эпик": "EpicGamesLauncher",
    "epic": "EpicGamesLauncher",
    "epic games": "EpicGamesLauncher",
    # -- GeForce Experience / NVIDIA --
    "нвидиа": "GeForce Experience",
    "nvidia": "GeForce Experience",
    "geforce experience": "GeForce Experience",
    # -- OBS Studio --
    "обс": "obs64",
    "obs": "obs64",
    "obs studio": "obs64",

    # =================================================================
    # Utilities
    # =================================================================
    # -- 7-Zip --
    "архиватор": "7zFM",
    "7zip": "7zFM",
    "7-zip": "7zFM",
    # -- WinRAR --
    "винрар": "winrar",
    "winrar": "winrar",
    # -- Recycle Bin --
    "корзина": "shell:RecycleBinFolder",
    "recycle bin": "shell:RecycleBinFolder",
    "кошик": "shell:RecycleBinFolder",
    # -- Snip & Sketch --
    "скриншотер": "ms-screenclip:",
    "snip and sketch": "ms-screenclip:",
    # -- Remote Desktop --
    "удалённый рабочий стол": "mstsc",
    "remote desktop": "mstsc",
    "rdp": "mstsc",

    # =================================================================
    # Additional common apps
    # =================================================================
    # -- Adobe Acrobat Reader --
    "акробат": "AcroRd32",
    "acrobat": "AcroRd32",
    "pdf reader": "AcroRd32",
    # -- Windows Media Player --
    "медиаплеер": "wmplayer",
    "медіаплеєр": "wmplayer",
    "media player": "wmplayer",
    "windows media player": "wmplayer",
    # -- WordPad --
    "вордпад": "wordpad",
    "wordpad": "wordpad",
    # -- Photos (Windows) --
    "фото": "ms-photos:",
    "photos": "ms-photos:",
    # -- Postman --
    "постман": "Postman",
    "postman": "Postman",
    # -- Docker Desktop --
    "докер": "Docker Desktop",
    "docker": "Docker Desktop",
    "docker desktop": "Docker Desktop",
    # -- Thunderbird --
    "тандерберд": "thunderbird",
    "thunderbird": "thunderbird",
    # -- PuTTY --
    "путти": "putty",
    "putty": "putty",
    # -- FileZilla --
    "файлзилла": "filezilla",
    "filezilla": "filezilla",
    # -- GIMP --
    "гимп": "gimp",
    "gimp": "gimp",
    # -- Audacity --
    "аудасити": "audacity",
    "audacity": "audacity",
    # -- VirtualBox --
    "виртуалбокс": "virtualbox",
    "virtualbox": "virtualbox",
}


def resolve_app_alias(name: str) -> str:
    """Resolve an informal / multilingual app name to a Windows-launchable target.

    Lookup is case-insensitive with stripped whitespace.
    Returns the canonical target when found, otherwise the original *name*
    unchanged — so callers always get a usable string and the existing
    resolution chain (fuzzy match → App Paths → PATH → shell) still runs.
    """
    key = name.strip().lower()
    return APP_ALIASES.get(key, name)
