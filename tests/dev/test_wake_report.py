"""Tests for jarvis.dev.wake_report log parser (Task 12 pt 3).

Pure text parsing -- no audio, no Qt, no openwakeword. Runs everywhere.
"""

from __future__ import annotations

from jarvis.dev.wake_report import format_report, parse_wake_log

# Realistic lines in jarvis.app's format:
#   %(asctime)s [%(levelname)s] [%(correlation_id)s] %(name)s: %(message)s
_P = "jarvis.audio.pipeline:"


def _trigger(ts: str, state: str = "IDLE") -> str:
    return (
        f"{ts} [INFO] [-] {_P} [wake] trigger score=0.9800 threshold=0.4400 "
        f"sensitivity=0.56 ms_since_tts=n/a state={state}"
    )


def _silence(ts: str) -> str:
    return f"{ts} [INFO] [-] {_P} listening silence timeout; returning to IDLE"


def _turn(ts: str) -> str:
    return f"{ts} [INFO] [abc12345] {_P} [turn] start: 1500 ms of audio"


def _suppressed(ts: str) -> str:
    return (
        f"{ts} [INFO] [-] {_P} [wake] suppressed: within 500 ms post-TTS "
        f"window (ms_since_tts=120)"
    )


def _interrupt(ts: str) -> str:
    return f"{ts} [INFO] [-] {_P} [wake] interrupted SPEAKING"


def test_empty_log_is_noop():
    r = parse_wake_log([])
    assert r.total_triggers == 0
    assert r.total_false == 0
    assert "no wake-word activity" in format_report(r)


def test_trigger_then_silence_is_false():
    r = parse_wake_log([_trigger("10:00:01"), _silence("10:00:07")])
    assert r.total_triggers == 1
    assert r.total_false == 1
    assert r.hours["10:00"].false_triggers == 1


def test_trigger_then_turn_is_real():
    r = parse_wake_log([_trigger("10:05:00"), _turn("10:05:01")])
    assert r.total_triggers == 1
    assert r.total_false == 0
    assert r.total_turns == 1
    # Buckets are per-hour, so 10:05:01 lands under the "10:00" hour.
    assert r.hours["10:00"].turns == 1


def test_suppressed_is_not_a_trigger():
    r = parse_wake_log([_suppressed("10:20:00")])
    assert r.total_triggers == 0
    assert r.total_suppressed == 1
    # Per-hour bucket: 10:20:00 lands under "10:00".
    assert r.hours["10:00"].suppressed == 1


def test_wake_interrupt_closes_as_real():
    # Trigger during SPEAKING immediately followed by an interrupt line is
    # an intentional barge-in-by-voice, never a false fire.
    r = parse_wake_log([_trigger("11:00:00", state="SPEAKING"), _interrupt("11:00:00")])
    assert r.total_triggers == 1
    assert r.total_false == 0


def test_pending_trigger_at_eof_counts_false():
    r = parse_wake_log([_trigger("11:30:00")])
    assert r.total_false == 1


def test_two_triggers_without_turn_both_false():
    # Second trigger supersedes the first unresolved one; both are false.
    r = parse_wake_log([_trigger("12:00:00"), _trigger("12:00:30")])
    assert r.total_triggers == 2
    assert r.total_false == 2


def test_per_hour_bucketing():
    lines = [
        _trigger("10:00:01"), _silence("10:00:07"),
        _trigger("10:59:00"), _turn("10:59:01"),
        _trigger("11:10:00"), _silence("11:10:06"),
    ]
    r = parse_wake_log(lines)
    assert r.hours["10:00"].triggers == 2
    assert r.hours["10:00"].false_triggers == 1
    assert r.hours["10:00"].turns == 1
    assert r.hours["11:00"].triggers == 1
    assert r.hours["11:00"].false_triggers == 1


def test_date_prefixed_timestamps_supported():
    lines = [
        "2026-10-30 09:15:00 [INFO] [-] " + _P + " [wake] trigger state=IDLE",
        "2026-10-30 09:15:30 [INFO] [-] " + _P + " [turn] start: 900 ms of audio",
    ]
    r = parse_wake_log(lines)
    assert "2026-10-30 09:00" in r.hours
    assert r.hours["2026-10-30 09:00"].turns == 1


def test_garbage_lines_do_not_crash():
    lines = [
        "not a log line at all",
        "",
        _trigger("13:00:00"),
        "   ",
        _turn("13:00:02"),
    ]
    r = parse_wake_log(lines)
    assert r.total_triggers == 1
    assert r.total_turns == 1


def test_format_report_contains_totals():
    r = parse_wake_log([_trigger("10:00:01"), _silence("10:00:05")])
    text = format_report(r)
    assert "TOTAL" in text
    assert "10:00" in text
