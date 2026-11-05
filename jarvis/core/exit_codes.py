"""Process exit codes shared by jarvis.app and the restart loop in __main__.

A module of its own so __main__ can read them without importing jarvis.app
(PySide6, audio) -- and so the restart-loop tests, which replace jarvis.app
with a stub, see the real values.
"""

from __future__ import annotations

# A broken environment (no wake-word model, the wrong Python): __main__
# does not restart on it, since a restart would fail the same way and show
# the same error box again (M2-2 / B-P2-6).
EXIT_ENVIRONMENT_ERROR = 3
