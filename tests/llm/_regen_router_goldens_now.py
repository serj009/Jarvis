#!/usr/bin/env python3
"""One-shot helper: regenerate golden files after voice-pattern changes.

Run from the repo root:
    python tests/llm/_regen_router_goldens_now.py

Then inspect the diff:
    git diff tests/llm/router_pattern_order_golden.json tests/llm/router_pattern_golden.json

Delete this file after using it (it's the same as _regen_router_goldens.py).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests.llm._regen_router_goldens import main

if __name__ == "__main__":
    main()
    print("\n✓ Goldens regenerated. Review the diff before committing.")
