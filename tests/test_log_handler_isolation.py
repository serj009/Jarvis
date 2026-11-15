"""A file log handler left on the root logger by one test is gone in the next.

B-P2-16: tests/ui/test_jarvis_app.py left the app's JSON log handler on the
root logger, and tests/core/test_jsonl_log_path.py then failed when it ran
afterwards. The two tests below run in file order: the first leaks a
handler on purpose, the second must not see it.
"""

from __future__ import annotations

import logging
from pathlib import Path

_LEAKED_NAME = "leaked-by-the-previous-test.jsonl"


def test_a_test_leaks_a_file_handler(tmp_path: Path):
    logging.getLogger().addHandler(logging.FileHandler(tmp_path / _LEAKED_NAME))


def test_the_next_test_does_not_see_it():
    leaked = [
        h for h in logging.getLogger().handlers
        if isinstance(h, logging.FileHandler) and h.baseFilename.endswith(_LEAKED_NAME)
    ]
    assert leaked == []
