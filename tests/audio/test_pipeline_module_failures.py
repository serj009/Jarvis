"""M2-3 (T12.3): a broken module does not take the voice loop down.

Before, an exception in the wake word or VAD on a frame crashed the frame
loop; it was restarted at once and crashed again on the next frame, logging
a traceback every time, and Jarvis stayed deaf without saying why.
"""

from __future__ import annotations

import pytest

from jarvis.audio.pipeline import MODULE_FAILURE_LIMIT
from jarvis.audio.protocols import VADEvent
from jarvis.core.events import NonFatalError
from jarvis.core.state_machine import ConversationalState
from tests.audio.test_pipeline import make_pipeline, wait_for_cs, yield_loop


class _BrokenWakeWord:
    def __init__(self, fail_first: int | None = None) -> None:
        self.feed_count = 0
        self.fail_first = fail_first  # None: always fails

    async def feed(self, frame):
        self.feed_count += 1
        if self.fail_first is None or self.feed_count <= self.fail_first:
            raise RuntimeError("onnx session lost")
        return None

    def reset(self):
        pass


async def test_broken_wake_word_is_switched_off_and_reported(caplog):
    pipeline, bus, sm, source, *_ = make_pipeline()
    broken = _BrokenWakeWord()
    pipeline._wake_word = broken
    problems: list[NonFatalError] = []
    spoken: list[str] = []
    bus.subscribe(NonFatalError, problems.append)
    # The proactive speech handler would need a real TTS turn; only record.
    pipeline._handle_proactive_speech = lambda e: spoken.append(e.text)  # type: ignore[method-assign]

    await pipeline.start()
    try:
        for _ in range(MODULE_FAILURE_LIMIT + 10):
            source.push()
        await yield_loop(60)
        # Fed until the limit, then left alone; the loop is still alive.
        assert broken.feed_count == MODULE_FAILURE_LIMIT
        assert pipeline._frame_task is not None and not pipeline._frame_task.done()
        assert sm.conversational_state is ConversationalState.IDLE
        assert [(p.module, p.issue) for p in problems] == [("wake_word", "module_failed")]
        assert "onnx session lost" in problems[0].actual
        assert any("push-to-talk" in t for t in spoken)
        # One traceback for the streak, not one per frame.
        assert sum("[module] wake_word failed" in r.message and r.exc_info is not None
                   for r in caplog.records) == 1
    finally:
        await pipeline.stop()


async def test_a_short_wake_word_glitch_is_not_reported():
    pipeline, bus, sm, source, *_ = make_pipeline()
    glitch = _BrokenWakeWord(fail_first=2)
    pipeline._wake_word = glitch
    problems: list[NonFatalError] = []
    bus.subscribe(NonFatalError, problems.append)
    await pipeline.start()
    try:
        for _ in range(MODULE_FAILURE_LIMIT + 5):
            source.push()
        await yield_loop(60)
        assert glitch.feed_count == MODULE_FAILURE_LIMIT + 5  # still fed
        assert problems == []
        assert not pipeline._wake_word_off
    finally:
        await pipeline.stop()


class _BrokenVAD:
    def __init__(self) -> None:
        self.feed_count = 0

    async def feed(self, frame):
        self.feed_count += 1
        raise RuntimeError("silero crashed")

    def reset(self):
        pass


async def test_vad_failure_ends_the_listening_turn_not_the_loop():
    pipeline, bus, sm, source, *_ = make_pipeline(detect_at=1)
    pipeline._vad = _BrokenVAD()
    await pipeline.start()
    try:
        source.push()  # wake
        await wait_for_cs(sm, ConversationalState.LISTENING)
        source.push()  # VAD raises
        await wait_for_cs(sm, ConversationalState.IDLE)
        assert pipeline._frame_task is not None and not pipeline._frame_task.done()
    finally:
        await pipeline.stop()


class _BrokenSTT:
    language = "ru"

    async def transcribe(self, audio):
        raise RuntimeError("CUDA error: out of memory")


async def test_stt_failure_says_what_failed():
    pipeline, bus, sm, source, _ww, _vad, _stt, tts = make_pipeline(
        detect_at=1, vad_script={2: VADEvent.SPEECH_STARTED, 4: VADEvent.ENDPOINT},
    )
    pipeline._stt = _BrokenSTT()
    await pipeline.start()
    try:
        source.push()
        await wait_for_cs(sm, ConversationalState.LISTENING)
        for _ in range(4):
            source.push()
        for _ in range(200):
            if tts.spoken:
                break
            await yield_loop(5)
        tts.finish_speaking()
        await wait_for_cs(sm, ConversationalState.IDLE)
        assert tts.spoken == ["Распознавание речи не сработало, сэр. Попробуйте ещё раз."]
    finally:
        await pipeline.stop()


@pytest.mark.parametrize("module", ["wake_word", "vad", "stt"])
def test_every_reportable_module_has_a_phrase(module):
    from jarvis.core.phrases import PHRASES

    assert set(PHRASES[f"module_down_{module}"]) == {"ru", "uk", "en"}


async def _speak_after_failing_turn(error: Exception) -> list[str]:
    pipeline, _bus, sm, source, _ww, _vad, _stt, tts = make_pipeline(
        detect_at=1, vad_script={2: VADEvent.SPEECH_STARTED, 4: VADEvent.ENDPOINT},
        transcription="какая сегодня погода",
    )

    async def failing_producer(text):
        raise error
        yield  # an async generator, like the real producer

    pipeline._response_producer = failing_producer
    await pipeline.start()
    try:
        source.push()
        await wait_for_cs(sm, ConversationalState.LISTENING)
        for _ in range(4):
            source.push()
        for _ in range(200):
            if tts.spoken:
                break
            await yield_loop(5)
        tts.finish_speaking()
        await wait_for_cs(sm, ConversationalState.IDLE)
        return tts.spoken
    finally:
        await pipeline.stop()


async def test_ollama_not_running_is_named():
    """Ollama down was spoken as the generic "something went wrong"."""
    from jarvis.llm.ollama_client import OllamaConnectionError

    spoken = await _speak_after_failing_turn(OllamaConnectionError("connection refused"))
    assert spoken == ["Языковая модель не отвечает, сэр. Проверьте, запущена ли Оллама."]


async def test_a_missing_model_is_named():
    from jarvis.llm.ollama_client import OllamaModelNotFoundError

    spoken = await _speak_after_failing_turn(OllamaModelNotFoundError("qwen3 not found"))
    assert spoken[0].startswith("Языковая модель не установлена в Олламе, сэр.")


async def test_a_failed_turn_is_reported_in_the_request_language():
    """The notice was English in a Russian conversation (the request context
    is gone by the time the error reaches the pipeline) and promised a retry
    that nothing does: "Something went wrong, sir. I'll try again."."""
    spoken = await _speak_after_failing_turn(RuntimeError("boom"))
    assert spoken == ["Что-то пошло не так, сэр. Попробуйте ещё раз."]
