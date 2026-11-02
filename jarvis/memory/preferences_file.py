"""Personal starter-memory file (``my_preferences.yaml``) in the user data dir.

The file holds the user's own facts (name, city, work schedule, ...), so it
must never live in the repository. Instead Jarvis creates an EMPTY template
next to the memory database the first time memory is set up:

    %APPDATA%/Jarvis/memory/my_preferences.yaml   (Windows)
    ~/.jarvis/memory/my_preferences.yaml          (other)

The user fills it in and runs ``python -m jarvis.personal_starter.import_starter``
once to load it into memory.

Rules:
- an existing file is NEVER overwritten (it is the user's data);
- failures are non-fatal: memory works without this file.
"""

from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger(__name__)

PREFERENCES_FILENAME = "my_preferences.yaml"

# Valid YAML with no personal data: every value is empty or commented out.
PREFERENCES_TEMPLATE = """\
# J.A.R.V.I.S. — стартовая личная память / starter personal memory
#
# Этот файл ЛИЧНЫЙ: он лежит только на вашем ПК и не попадает в git.
# This file is PERSONAL: it stays on your PC and is never committed.
#
# Заполните нужное и загрузите один раз:
#   python -m jarvis.personal_starter.import_starter

preferences: {}
#  language: "auto"          # auto / ru / uk / en
#  response_style: "concise"
#  time_format_ru: "24h"
#  time_format_uk: "24h"
#  time_format_en: "12h"
#  announce_actions: true

# profile:                   # -> user_profile.json (всегда в контексте модели)
#   name: "..."
#   city: "..."
#   country: "..."
#   timezone: "Europe/Warsaw"
#   occupation: "..."
#   interests: ["..."]

facts: []
#  - content: "Меня зовут ..."
#    category: "personal/identity"
#    tags: ["name"]
#    is_permanent: true

projects: []
#  - name: "..."
#    description: "..."
#    status: "active"

# work_schedule:
#   type: "shift"
#   rotation: "monthly"
#   shifts:
#     day:
#       days: ["wed", "thu", "fri", "sat"]
#       hours: "10.5h"
"""


def default_preferences_path() -> Path:
    """``<data dir>/memory/my_preferences.yaml`` (same folder as memory.db)."""
    from jarvis.paths import data_path

    return data_path("memory", PREFERENCES_FILENAME)


def ensure_preferences_file(path: Path | None = None) -> bool:
    """Create the empty template at *path* if it does not exist yet.

    Returns True when a new template was written, False when the file was
    already there or could not be created. Never raises.
    """
    target = path or default_preferences_path()
    try:
        if target.exists():
            return False
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(PREFERENCES_TEMPLATE, encoding="utf-8")
    except OSError:
        log.warning("could not create %s (non-fatal)", target, exc_info=True)
        return False
    log.info("created personal preferences template: %s", target)
    return True
