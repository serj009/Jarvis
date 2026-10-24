from __future__ import annotations

import logging
import time
import traceback
from pathlib import Path

log = logging.getLogger(__name__)

MAX_RESTARTS = 3
RESTART_COOLDOWN = 5  # seconds


def _frozen_log_path() -> Path:
    # Single data-dir resolver (jarvis.paths); layout unchanged: logs/jarvis.log.
    from jarvis.paths import data_path

    return data_path("logs", "jarvis.log")


def _write_crash_log(exc: BaseException) -> Path:
    path = _frozen_log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write("\n--- crash ---\n")
        traceback.print_exception(exc, file=f)
    return path


def _show_error_box(message: str) -> None:
    try:
        from jarvis import platform as platform_ops

        platform_ops.message_box(message, title="Jarvis", flags=0x10)
    except Exception:
        pass


def main() -> None:
    from jarvis.paths import is_frozen

    if is_frozen():
        log_path = _frozen_log_path()
        log_path.parent.mkdir(parents=True, exist_ok=True)
        logging.basicConfig(
            filename=str(log_path),
            level=logging.INFO,
            format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        )

    restarts = 0
    while True:
        try:
            from jarvis.app import run

            exit_code = run()
            if exit_code == 0:
                # Clean exit (user quit) — do NOT restart.
                raise SystemExit(0)
            # Non-zero = crash / error; attempt restart.
            restarts += 1
            log.warning(
                "JARVIS exited with code %d, restart %d/%d …",
                exit_code, restarts, MAX_RESTARTS,
            )
            if restarts >= MAX_RESTARTS:
                log.error(
                    "Max restarts (%d) reached, giving up", MAX_RESTARTS,
                )
                raise SystemExit(exit_code)
            time.sleep(RESTART_COOLDOWN)
        except KeyboardInterrupt:
            # User pressed Ctrl+C — clean exit, no restart.
            break
        except SystemExit:
            raise
        except Exception as exc:
            log_file = _write_crash_log(exc)
            restarts += 1
            log.warning(
                "JARVIS crashed (%s), restart %d/%d …",
                exc, restarts, MAX_RESTARTS,
            )
            if restarts >= MAX_RESTARTS:
                log.error(
                    "Max restarts (%d) reached after crash, giving up",
                    MAX_RESTARTS,
                )
                if is_frozen():
                    _show_error_box(
                        f"Jarvis failed to start:\n\n{exc}\n\nDetails: {log_file}"
                    )
                raise
            time.sleep(RESTART_COOLDOWN)


if __name__ == "__main__":
    main()
