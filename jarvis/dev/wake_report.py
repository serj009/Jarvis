"""Offline wake-word false-trigger report (Task 12 pt 3).

Parses a Jarvis log file and summarises, per clock-hour:

* how many times the wake word triggered (``[wake] trigger`` lines), and
* how many of those were *false* -- a trigger that led straight to a
  ``listening silence timeout`` with no actual turn (no ``[turn] start``)
  in between, i.e. Jarvis woke up but the user never spoke.

Suppressed self-triggers (``[wake] suppressed``) are counted separately so
you can see how much the post-TTS pause is doing.

No personal data is read or emitted: the report only ever touches the
structural ``[wake]``/``[turn]``/``silence timeout`` log markers and their
timestamps -- never transcripts, never audio.

Usage::

    python -m jarvis.dev.wake_report --log path/to/jarvis.log
    python -m jarvis.dev.wake_report            # defaults to data_dir/logs/jarvis.log

The log format written by ``jarvis.app`` is::

    %(asctime)s [%(levelname)s] [%(correlation_id)s] %(name)s: %(message)s

with ``asctime`` = ``%H:%M:%S`` (the frozen ``__main__`` launcher omits the
correlation-id field). Both shapes are accepted; a line whose timestamp
cannot be parsed is bucketed under the most recent hour seen so a partial
line never crashes the report.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field

# Leading "HH:MM:SS" (optionally "YYYY-MM-DD HH:MM:SS" if the operator used a
# full datefmt). We only need the hour bucket, so capture up to the hour.
_TS_RE = re.compile(
    r"^(?:(?P<date>\d{4}-\d{2}-\d{2})[ T])?(?P<hour>\d{2}):(?P<min>\d{2}):(?P<sec>\d{2})"
)

# Structural markers -- substring tests, format-string stable (see pipeline.py).
_TRIGGER = "[wake] trigger"
_SUPPRESSED = "[wake] suppressed"
_SILENCE_TIMEOUT = "listening silence timeout"
_TURN_START = "[turn] start"
_WAKE_INTERRUPT = "[wake] interrupted"


@dataclass
class HourStat:
    triggers: int = 0
    suppressed: int = 0
    false_triggers: int = 0
    turns: int = 0


@dataclass
class WakeReport:
    hours: dict[str, HourStat] = field(default_factory=lambda: defaultdict(HourStat))

    @property
    def total_triggers(self) -> int:
        return sum(h.triggers for h in self.hours.values())

    @property
    def total_false(self) -> int:
        return sum(h.false_triggers for h in self.hours.values())

    @property
    def total_suppressed(self) -> int:
        return sum(h.suppressed for h in self.hours.values())

    @property
    def total_turns(self) -> int:
        return sum(h.turns for h in self.hours.values())


def _hour_bucket(line: str, fallback: str | None) -> str | None:
    m = _TS_RE.match(line)
    if not m:
        return fallback
    date = m.group("date")
    hour = m.group("hour")
    return f"{date} {hour}:00" if date else f"{hour}:00"


def parse_wake_log(lines: object) -> WakeReport:
    """Build a :class:`WakeReport` from an iterable of log lines.

    Chronological walk: a ``[wake] trigger`` opens a *pending* wake. If the
    next relevant marker is a ``listening silence timeout`` the wake is
    counted false; a ``[turn] start`` (or another trigger / wake interrupt)
    closes the pending wake as a real one. Robust to truncated/garbage
    lines -- anything unrecognised is skipped.
    """
    report = WakeReport()
    last_hour: str | None = None
    pending_hour: str | None = None  # hour-bucket of an unresolved trigger

    for raw in lines:
        line = raw.rstrip("\n")
        if not line:
            continue
        hour = _hour_bucket(line, last_hour)
        if hour is not None:
            last_hour = hour

        if _SUPPRESSED in line:
            # Not a real trigger -- it never reached the handler.
            if hour is not None:
                report.hours[hour].suppressed += 1
            continue

        if _TRIGGER in line:
            # A previous pending trigger that never saw a turn and is now
            # superseded by a fresh trigger counts as false (woke, nothing
            # said, woke again).
            if pending_hour is not None:
                report.hours[pending_hour].false_triggers += 1
            if hour is not None:
                report.hours[hour].triggers += 1
            pending_hour = hour
            continue

        if _TURN_START in line:
            if hour is not None:
                report.hours[hour].turns += 1
            # A real turn closes any pending wake as legitimate.
            pending_hour = None
            continue

        if _SILENCE_TIMEOUT in line:
            if pending_hour is not None:
                report.hours[pending_hour].false_triggers += 1
                pending_hour = None
            continue

        if _WAKE_INTERRUPT in line:
            # Interrupt-by-voice during SPEAKING/THINKING is a real,
            # intentional wake; close any pending trigger as legitimate.
            pending_hour = None
            continue

    # A trigger still pending at EOF never produced a turn -> false.
    if pending_hour is not None:
        report.hours[pending_hour].false_triggers += 1

    return report


def format_report(report: WakeReport) -> str:
    out: list[str] = []
    out.append("Wake-word trigger report")
    out.append("=" * 60)
    out.append(
        f"{'hour':<16}{'triggers':>10}{'false':>8}{'false%':>8}"
        f"{'suppr':>8}{'turns':>8}"
    )
    out.append("-" * 60)
    for hour in sorted(report.hours):
        s = report.hours[hour]
        pct = (s.false_triggers / s.triggers * 100.0) if s.triggers else 0.0
        out.append(
            f"{hour:<16}{s.triggers:>10}{s.false_triggers:>8}{pct:>7.0f}%"
            f"{s.suppressed:>8}{s.turns:>8}"
        )
    out.append("-" * 60)
    tt = report.total_triggers
    tf = report.total_false
    pct = (tf / tt * 100.0) if tt else 0.0
    out.append(
        f"{'TOTAL':<16}{tt:>10}{tf:>8}{pct:>7.0f}%"
        f"{report.total_suppressed:>8}{report.total_turns:>8}"
    )
    if not report.hours:
        out.append("(no wake-word activity found in log)")
    return "\n".join(out)


def _default_log_path() -> str:
    try:
        from jarvis.paths import data_path

        return str(data_path("logs", "jarvis.log"))
    except Exception:
        return "jarvis.log"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m jarvis.dev.wake_report",
        description="Summarise wake-word triggers and false fires per hour.",
    )
    parser.add_argument(
        "--log",
        default=None,
        help="Path to the Jarvis log file (default: <data_dir>/logs/jarvis.log).",
    )
    args = parser.parse_args(argv)

    log_path = args.log or _default_log_path()
    try:
        with open(log_path, encoding="utf-8", errors="replace") as fh:
            report = parse_wake_log(fh)
    except FileNotFoundError:
        print(f"log file not found: {log_path}", file=sys.stderr)
        return 1
    except OSError as e:
        print(f"could not read log file {log_path}: {e}", file=sys.stderr)
        return 1

    print(format_report(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
