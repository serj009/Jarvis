from __future__ import annotations

import argparse
import logging
import time
import traceback
from pathlib import Path

log = logging.getLogger(__name__)

MAX_RESTARTS = 3
RESTART_COOLDOWN = 5  # seconds


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse jarvis CLI args. Only diagnostics flags exist; absent flags mean
    'launch the assistant normally'."""
    parser = argparse.ArgumentParser(
        prog="jarvis",
        description="Jarvis voice assistant.",
    )
    parser.add_argument(
        "--doctor", action="store_true",
        help="Run environment self-diagnostics and exit (does not launch).",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="With --doctor: print the report as JSON.",
    )
    parser.add_argument(
        "--redact", action="store_true",
        help="With --doctor: replace the home dir and username with ~ / <user>.",
    )
    parser.add_argument(
        "--deep", action="store_true",
        help="With --doctor: also run a 1s silence STT + short TTS-to-buffer probe.",
    )
    return parser.parse_args(argv)


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


def _cli(argv: list[str] | None = None) -> None:
    """Real process entry point (``python -m jarvis``).

    Parses CLI args HERE — not inside main() — so main() stays a plain no-arg
    function the restart-loop tests can call directly without argparse
    consuming pytest's own argv. ``--doctor`` runs read-only diagnostics and
    exits without launching the assistant.
    """
    # Task C §2: install uncaught-exception + faulthandler hooks as early as
    # possible (real process only — tests call main() directly). Idempotent,
    # never raises.
    try:
        from jarvis.core.diagnostics import install_crash_handlers

        install_crash_handlers()
    except Exception:
        pass

    args = _parse_args(argv)
    if args.doctor:
        from jarvis.core.doctor import run_doctor

        code = run_doctor(
            as_json=args.json, do_redact=args.redact, deep=args.deep
        )
        raise SystemExit(code)

    main()


if __name__ == "__main__":
    _cli()
