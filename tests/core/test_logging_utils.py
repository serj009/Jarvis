"""Tests for jarvis.core.logging_utils (T1.2 Structured Logging)."""

from __future__ import annotations

import json
import logging
import time

import pytest

from jarvis.core.logging_utils import JsonFormatter, PipelineTracker, log_latency
from jarvis.core.request_context import (
    NO_CORRELATION_ID,
    correlation_id,
    new_correlation_id,
    turn_started_at,
)


# ---------------------------------------------------------------------------
# JsonFormatter
# ---------------------------------------------------------------------------


class TestJsonFormatter:
    def test_output_is_valid_json(self) -> None:
        fmt = JsonFormatter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="hello %s",
            args=("world",),
            exc_info=None,
        )
        line = fmt.format(record)
        data = json.loads(line)
        assert data["msg"] == "hello world"
        assert data["level"] == "INFO"
        assert data["logger"] == "test"
        assert "ts" in data

    def test_correlation_id_present(self) -> None:
        fmt = JsonFormatter()
        record = logging.LogRecord(
            name="x", level=logging.DEBUG, pathname="", lineno=0,
            msg="test", args=(), exc_info=None,
        )
        record.correlation_id = "abc12345"  # type: ignore[attr-defined]
        data = json.loads(fmt.format(record))
        assert data["correlation_id"] == "abc12345"

    def test_exception_included(self) -> None:
        fmt = JsonFormatter()
        try:
            raise ValueError("boom")
        except ValueError:
            import sys
            record = logging.LogRecord(
                name="x", level=logging.ERROR, pathname="", lineno=0,
                msg="fail", args=(), exc_info=sys.exc_info(),
            )
        data = json.loads(fmt.format(record))
        assert "exc" in data
        assert "boom" in data["exc"]


# ---------------------------------------------------------------------------
# @log_latency
# ---------------------------------------------------------------------------


class TestLogLatency:
    @pytest.mark.asyncio()
    async def test_async_function_logs(self, caplog: pytest.LogCaptureFixture) -> None:
        @log_latency
        async def slow_op() -> str:
            return "done"

        with caplog.at_level(logging.DEBUG, logger="jarvis.core.logging_utils"):
            result = await slow_op()
        assert result == "done"
        assert any("slow_op" in r.message and "completed" in r.message for r in caplog.records)

    def test_sync_function_logs(self, caplog: pytest.LogCaptureFixture) -> None:
        @log_latency
        def fast_op() -> int:
            return 42

        with caplog.at_level(logging.DEBUG, logger="jarvis.core.logging_utils"):
            result = fast_op()
        assert result == 42
        assert any("fast_op" in r.message for r in caplog.records)

    @pytest.mark.asyncio()
    async def test_exception_still_raised(self) -> None:
        @log_latency
        async def failing() -> None:
            raise RuntimeError("fail")

        with pytest.raises(RuntimeError, match="fail"):
            await failing()


# ---------------------------------------------------------------------------
# PipelineTracker
# ---------------------------------------------------------------------------


class TestPipelineTracker:
    def test_start_stop_records_time(self) -> None:
        tracker = PipelineTracker()
        tracker.start("stt")
        time.sleep(0.01)
        elapsed = tracker.stop("stt")
        assert elapsed >= 5  # at least 5ms (generous for CI)

    def test_stop_without_start_raises(self) -> None:
        tracker = PipelineTracker()
        with pytest.raises(KeyError, match="stt"):
            tracker.stop("stt")

    def test_total_ms(self) -> None:
        tracker = PipelineTracker()
        tracker.start("a")
        tracker.stop("a")
        tracker.start("b")
        tracker.stop("b")
        assert tracker.total_ms >= 0

    def test_summary_format(self) -> None:
        tracker = PipelineTracker()
        tracker.start("stt")
        tracker.stop("stt")
        tracker.start("llm")
        tracker.stop("llm")
        summary = tracker.summary()
        assert "stt=" in summary
        assert "llm=" in summary
        assert "total=" in summary
        assert "[" in summary  # correlation_id bracket

    def test_reset(self) -> None:
        tracker = PipelineTracker()
        tracker.start("x")
        tracker.stop("x")
        tracker.reset()
        assert tracker.total_ms == 0

    def test_as_dict(self) -> None:
        tracker = PipelineTracker()
        tracker.start("stt")
        tracker.stop("stt")
        d = tracker.as_dict()
        assert "stt" in d
        assert isinstance(d["stt"], float)
