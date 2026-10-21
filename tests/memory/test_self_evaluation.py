"""Tests for jarvis.memory.self_evaluation (auto-critic, Layer 1)."""

from __future__ import annotations

from jarvis.memory.self_evaluation import (
    InteractionTrace,
    TraceStep,
    build_trace,
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
        import json

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
