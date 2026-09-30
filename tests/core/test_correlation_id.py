"""Roadmap T1.2: every log line of one voice turn carries the same
correlation id; different turns get different ids; outside a turn it is "-"."""

from __future__ import annotations

import asyncio
import logging

from jarvis.core.request_context import (
    NO_CORRELATION_ID,
    CorrelationIdFilter,
    correlation_id,
    new_correlation_id,
)


class _ListHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.addFilter(CorrelationIdFilter())
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


def _capture() -> tuple[logging.Logger, _ListHandler]:
    logger = logging.getLogger("jarvis.test.correlation")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    handler = _ListHandler()
    logger.handlers = [handler]
    return logger, handler


def test_new_ids_are_short_and_distinct():
    ids = {new_correlation_id() for _ in range(100)}
    assert len(ids) == 100
    assert all(len(i) == 8 for i in ids)


def test_outside_turn_id_is_dash():
    logger, handler = _capture()
    logger.info("idle")
    assert handler.records[0].correlation_id == NO_CORRELATION_ID


def test_each_task_has_its_own_id_including_threads():
    logger, handler = _capture()

    async def turn(name: str) -> str:
        cid = new_correlation_id()
        token = correlation_id.set(cid)
        try:
            logger.info("%s start", name)
            await asyncio.sleep(0)
            # STT runs via asyncio.to_thread: the id must follow it there.
            await asyncio.to_thread(logger.info, "%s in thread", name)
            logger.info("%s end", name)
        finally:
            correlation_id.reset(token)
        return cid

    async def main() -> list[str]:
        return list(await asyncio.gather(turn("a"), turn("b")))

    cid_a, cid_b = asyncio.run(main())

    assert cid_a != cid_b
    by_turn: dict[str, set[str]] = {"a": set(), "b": set()}
    for rec in handler.records:
        by_turn[rec.getMessage()[0]].add(rec.correlation_id)
    assert by_turn == {"a": {cid_a}, "b": {cid_b}}
    # Nothing leaked into the caller's context.
    assert correlation_id.get() == NO_CORRELATION_ID
