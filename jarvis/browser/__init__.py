"""Browser control package — tab management, navigation, content reading.

Provides a ``BrowserController`` with two modes:

- **Simple mode** (zero dependencies): uses ``webbrowser.open()`` and URL
  construction.  Always available.
- **Advanced mode** (optional Selenium): full tab control, navigation, DOM
  access.  Activated lazily the first time the caller needs it, and only
  if ``selenium`` is installed (``pip install jarvis[browser]``).

Advanced mode is never *required* — every public method degrades to simple
mode or returns a clear "Selenium not available" result.
"""

from jarvis.browser.browser_controller import BrowserController

__all__ = ["BrowserController"]
