"""Tests for jarvis.memory.self_evaluation (auto-critic, Layer 1)."""

from __future__ import annotations

import json

from jarvis.memory.self_evaluation import (
    InteractionTrace,
    SelfEvaluationScheduler,
    TraceStep,
    build_critic_messages,
    build_trace,
    parse_critic_response,
    render_report_markdown,
    run_evaluation,
    strip_secret_args,
    strip_secrets,
)

# Built at runtime so the test source itself contains no literal secret.
_PW = "pass" + "word"


class TestStripSecrets:
    def test_leaves_normal_speech_untouched(self) -> None:
        for phrase in [
            "открой хром",
            "what's the weather today",
            "запусти игру Mafia 2 через Steam",
            "какая погода в Варшаве",
            "set a timer for 10 minutes",
        ]:
            assert strip_secrets(phrase) == phrase

    def test_redacts_labelled_password(self) -> None:
        out = strip_secrets(_PW + "=hunter2")
        assert "hunter2" not in out
        assert "[REDACTED]" in out

    def test_redacts_api_key_token(self) -> None:
        out = strip_secrets("my api_key is sk-abc123def456ghi789xyz")
        assert "sk-abc123def456ghi789xyz" not in out
        assert "[REDACTED]" in out

    def test_redacts_bearer_header(self) -> None:
        out = strip_secrets("use token: ghp_ABCDEFGH12345678")
        assert "ghp_ABCDEFGH12345678" not in out

    def test_empty_is_safe(self) -> None:
        assert strip_secrets("") == ""


class TestStripSecretArgs:
    def test_redacts_credential_named_values(self) -> None:
        args = {"username": "serhii", "password": "hunter2", "query": "weather"}
        clean = strip_secret_args(args)
        assert clean["username"] == "[REDACTED]"
        assert clean["password"] == "[REDACTED]"
        assert clean["query"] == "weather"  # normal arg preserved

    def test_scrubs_secret_inside_normal_arg_value(self) -> None:
        args = {"note": "my api_key is sk-abc123def456ghi789xyz"}
        clean = strip_secret_args(args)
        assert "sk-abc123def456ghi789xyz" not in clean["note"]

    def test_non_string_values_pass_through(self) -> None:
        args = {"count": 5, "flag": True}
        assert strip_secret_args(args) == {"count": 5, "flag": True}

    def test_empty_args(self) -> None:
        assert strip_secret_args({}) == {}


class TestBuildTrace:
    def test_assembles_full_trace(self) -> None:
        trace = build_trace(
            correlation_id="c1",
            utterance="открой хром",
            recognised_text="открой хром",
            steps=[TraceStep("open_app", {"name": "chrome"}, "Opened chrome", True)],
            final_answer="Done, sir.",
            timestamp="2026-10-21T10:00:00+00:00",
        )
        assert isinstance(trace, InteractionTrace)
        assert trace.correlation_id == "c1"
        assert trace.utterance == "открой хром"
        assert len(trace.steps) == 1
        assert trace.steps[0].tool_name == "open_app"
        assert trace.final_answer == "Done, sir."

    def test_strips_secrets_from_every_field(self) -> None:
        trace = build_trace(
            correlation_id="c2",
            utterance="log into my bank with " + _PW + "=hunter2",
            steps=[
                TraceStep(
                    "type_into_active_window",
                    {"password": "hunter2", "text": "api_key sk-abc123def456ghijk"},
                    "typed",
                    True,
                ),
            ],
            final_answer="done",
        )
        assert "hunter2" not in trace.utterance
        assert trace.steps[0].args["password"] == "[REDACTED]"
        assert "sk-abc123def456ghijk" not in trace.steps[0].args["text"]

    def test_to_dict_is_serialisable(self) -> None:

        trace = build_trace(
            correlation_id="c3",
            utterance="hi",
            steps=[TraceStep("get_time", {}, "15:00", True)],
        )
        # Must round-trip through JSON (it will be sent over an API later).
        dumped = json.dumps(trace.to_dict())
        assert "get_time" in dumped

    def test_empty_steps_ok(self) -> None:
        trace = build_trace(correlation_id="c4", utterance="thanks")
        assert trace.steps == []


class TestSelfEvaluationConfig:
    def test_defaults_off_and_local(self) -> None:
        from jarvis.core.config import SelfEvaluationConfig

        cfg = SelfEvaluationConfig()
        assert cfg.enabled is False  # opt-in only
        assert cfg.save_insights_to_memory is False
        assert cfg.interval_hours == 24.0

    def test_present_on_jarvis_config(self) -> None:
        from jarvis.core.config import JarvisConfig

        cfg = JarvisConfig()
        assert cfg.self_evaluation.enabled is False

    def test_api_key_is_an_encrypted_field(self) -> None:
        from jarvis.core.config import _SECRET_FIELD_PATHS

        assert ("self_evaluation", "api_key") in _SECRET_FIELD_PATHS


class TestMigrationV37:
    def test_migrate_adds_self_evaluation(self) -> None:
        from jarvis.core.migrations import _migrate_v36_to_v37

        data = _migrate_v36_to_v37({"schema_version": 36})
        assert data["schema_version"] == 37
        se = data["self_evaluation"]
        assert se["enabled"] is False
        assert se["provider"] == "groq"
        assert se["save_insights_to_memory"] is False

    def test_migration_registered(self) -> None:
        from jarvis.core.migrations import MIGRATIONS

        assert 36 in MIGRATIONS


# ---------------------------------------------------------------------------
# Layer 2: critic prompt, evaluation, report, scheduler
# ---------------------------------------------------------------------------

def _sample_traces():
    return [
        build_trace(
            correlation_id="c1",
            utterance="открой хром",
            steps=[TraceStep("open_app", {"name": "chrome"}, "Opened chrome", True)],
            final_answer="Done, sir.",
        )
    ]


class TestBuildCriticMessages:
    def test_has_system_and_user(self) -> None:
        msgs = build_critic_messages(_sample_traces())
        assert msgs[0]["role"] == "system"
        assert msgs[1]["role"] == "user"
        assert "open_app" in msgs[1]["content"]


class TestParseCriticResponse:
    def test_parses_plain_json(self) -> None:
        raw = json.dumps({
            "evaluations": [{"correlation_id": "c1", "score": 9,
                             "verdict": "good", "issues": [], "suggestions": []}],
            "overall": "solid",
        })
        rep = parse_critic_response(raw)
        assert rep["evaluations"][0]["score"] == 9
        assert rep["overall"] == "solid"

    def test_parses_fenced_json(self) -> None:
        raw = "```json\n" + json.dumps({"evaluations": [], "overall": "x"}) + "\n```"
        rep = parse_critic_response(raw)
        assert rep["overall"] == "x"

    def test_extracts_json_amid_prose(self) -> None:
        raw = 'Here is my review: {"evaluations": [], "overall": "ok"} hope it helps'
        rep = parse_critic_response(raw)
        assert rep["overall"] == "ok"

    def test_garbage_returns_error_report(self) -> None:
        rep = parse_critic_response("not json at all")
        assert "error" in rep
        assert rep["evaluations"] == []

    def test_empty_returns_error_report(self) -> None:
        rep = parse_critic_response("")
        assert "error" in rep


class TestRunEvaluation:
    async def test_happy_path(self) -> None:
        async def fake_send(messages):
            return json.dumps({
                "evaluations": [{"correlation_id": "c1", "score": 8,
                                 "verdict": "ok", "issues": [], "suggestions": ["x"]}],
                "overall": "fine",
            })

        rep = await run_evaluation(_sample_traces(), fake_send)
        assert rep["trace_count"] == 1
        assert rep["evaluations"][0]["score"] == 8

    async def test_empty_traces_no_call(self) -> None:
        called = False

        async def fake_send(messages):
            nonlocal called
            called = True
            return "{}"

        rep = await run_evaluation([], fake_send)
        assert called is False
        assert "error" in rep

    async def test_failing_send_is_non_fatal(self) -> None:
        async def boom(messages):
            raise RuntimeError("network down")

        rep = await run_evaluation(_sample_traces(), boom)
        assert "error" in rep
        assert "network down" in rep["error"]

    async def test_respects_max_traces(self) -> None:
        traces = [
            build_trace(correlation_id=f"c{i}", utterance="hi")
            for i in range(10)
        ]
        seen = {}

        async def fake_send(messages):
            # Count traces in the payload.
            seen["payload"] = messages[1]["content"]
            return json.dumps({"evaluations": [], "overall": ""})

        rep = await run_evaluation(traces, fake_send, max_traces=3)
        assert rep["trace_count"] == 3
        assert seen["payload"].count("correlation_id") == 3


class TestRenderReport:
    def test_renders_sections(self) -> None:
        report = {
            "trace_count": 1,
            "overall": "Looks good overall.",
            "evaluations": [{
                "correlation_id": "c1", "score": 7, "verdict": "mostly fine",
                "issues": ["slow"], "suggestions": ["cache it"],
            }],
        }
        md = render_report_markdown(report, generated_at="2026-10-21T10:00:00+00:00")
        assert "# Jarvis self-evaluation report" in md
        assert "Looks good overall." in md
        assert "score 7/10" in md
        assert "- slow" in md
        assert "- cache it" in md

    def test_renders_error_banner(self) -> None:
        md = render_report_markdown({"error": "no key", "evaluations": []})
        assert "no key" in md


class TestScheduler:
    async def test_disabled_start_is_noop(self) -> None:
        sched = SelfEvaluationScheduler(
            enabled=False, interval_hours=1, max_traces_per_run=5,
            trace_source=lambda: _sample_traces(),
            send_fn=None, report_dir="/tmp/nope",
        )
        sched.start()  # must not raise, must not launch
        assert sched._task is None

    async def test_run_once_writes_report(self, tmp_path) -> None:
        async def fake_send(messages):
            return json.dumps({
                "evaluations": [{"correlation_id": "c1", "score": 9,
                                 "verdict": "great", "issues": [], "suggestions": []}],
                "overall": "all good",
            })

        sched = SelfEvaluationScheduler(
            enabled=True, interval_hours=1, max_traces_per_run=5,
            trace_source=_sample_traces, send_fn=fake_send,
            report_dir=tmp_path / "reports",
        )
        report = await sched.run_once()
        assert report["evaluations"][0]["score"] == 9
        assert sched.last_report_path is not None
        assert sched.last_report_path.exists()
        assert "all good" in sched.last_report_path.read_text(encoding="utf-8")
