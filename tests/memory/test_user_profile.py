"""Tests for jarvis.memory.user_profile — get/set, update_from_fact, for_prompt."""

from __future__ import annotations

import json

import pytest

from jarvis.memory.user_profile import UserProfile


@pytest.fixture()
def profile(tmp_path) -> UserProfile:
    return UserProfile(path=tmp_path / "user_profile.json")


class TestUserProfile:
    def test_starts_with_defaults(self, profile) -> None:
        assert profile.get("name") is None
        assert profile.get("city") is None
        assert profile.get("language") == "auto"
        assert profile.get("interests") == []

    def test_set_and_get_known_key(self, profile) -> None:
        profile.set("name", "Serhii")
        assert profile.get("name") == "Serhii"

    def test_set_and_get_custom_key(self, profile) -> None:
        profile.set("favorite_game", "Stalker 2")
        assert profile.get("favorite_game") == "Stalker 2"

    def test_set_persists(self, tmp_path) -> None:
        path = tmp_path / "profile.json"

        p1 = UserProfile(path=path)
        p1.set("name", "Serhii")
        p1.set("city", "Zielona Góra")

        p2 = UserProfile(path=path)
        assert p2.get("name") == "Serhii"
        assert p2.get("city") == "Zielona Góra"

    def test_update_from_fact_name_ru(self, profile) -> None:
        profile.update_from_fact("меня зовут Серж", "identity")
        assert profile.get("name") == "Серж"

    def test_update_from_fact_name_en(self, profile) -> None:
        profile.update_from_fact("my name is Serhii", "identity")
        assert profile.get("name") == "Serhii"

    def test_update_from_fact_name_uk(self, profile) -> None:
        profile.update_from_fact("мене звати Сергій", "identity")
        assert profile.get("name") == "Сергій"

    def test_update_from_fact_call_me(self, profile) -> None:
        profile.update_from_fact("call me Alex", "identity")
        assert profile.get("name") == "Alex"

    def test_update_from_fact_city_ru(self, profile) -> None:
        profile.update_from_fact("я живу в Зелёна Гура", "location")
        assert profile.get("city") == "Зелёна Гура"

    def test_update_from_fact_city_en(self, profile) -> None:
        profile.update_from_fact("I live in Warsaw", "location")
        assert profile.get("city") == "Warsaw"

    def test_update_from_fact_from_en(self, profile) -> None:
        profile.update_from_fact("I'm from Berlin", "location")
        assert profile.get("city") == "Berlin"

    def test_update_from_fact_occupation(self, profile) -> None:
        profile.update_from_fact("я работаю в Amazon", "work")
        assert profile.get("occupation") == "в amazon"

    def test_update_from_fact_no_match(self, profile) -> None:
        profile.update_from_fact("some random text", "general")
        assert profile.get("name") is None
        assert profile.get("city") is None

    def test_for_prompt_empty_returns_none(self, profile) -> None:
        assert profile.for_prompt() is None

    def test_for_prompt_with_name(self, profile) -> None:
        profile.set("name", "Serhii")
        result = profile.for_prompt()
        assert result is not None
        assert "User's name: Serhii" in result
        assert result.startswith("[User profile:]")

    def test_for_prompt_with_location(self, profile) -> None:
        profile.set("city", "Warsaw")
        profile.set("country", "Poland")
        result = profile.for_prompt()
        assert "Location: Warsaw, Poland" in result

    def test_for_prompt_with_city_only(self, profile) -> None:
        profile.set("city", "Berlin")
        result = profile.for_prompt()
        assert "Location: Berlin" in result
        assert "Poland" not in (result or "")

    def test_for_prompt_with_occupation(self, profile) -> None:
        profile.set("occupation", "works at Amazon")
        result = profile.for_prompt()
        assert "Occupation: works at Amazon" in result

    def test_for_prompt_with_interests(self, profile) -> None:
        profile.set("interests", ["gaming", "programming", "cooking"])
        result = profile.for_prompt()
        assert "Interests: gaming, programming, cooking" in result

    def test_for_prompt_with_custom(self, profile) -> None:
        profile.set("pet", "cat named Luna")
        result = profile.for_prompt()
        assert "pet: cat named Luna" in result

    def test_for_prompt_full(self, profile) -> None:
        profile.set("name", "Serhii")
        profile.set("city", "Zielona Góra")
        profile.set("country", "Poland")
        profile.set("occupation", "works at Amazon")
        result = profile.for_prompt()
        assert result is not None
        assert "[User profile:]" in result
        assert "User's name: Serhii" in result
        assert "Location: Zielona Góra, Poland" in result
        assert "Occupation: works at Amazon" in result

    def test_corrupted_file_uses_defaults(self, tmp_path) -> None:
        path = tmp_path / "profile.json"
        path.write_text("{{{broken", encoding="utf-8")

        p = UserProfile(path=path)
        assert p.get("name") is None
        # Should still work
        p.set("name", "Test")
        assert p.get("name") == "Test"

    def test_interests_limit_in_prompt(self, profile) -> None:
        profile.set("interests", ["a", "b", "c", "d", "e", "f", "g"])
        result = profile.for_prompt()
        # Only first 5 should be shown
        assert "f" not in (result or "")
        assert "g" not in (result or "")

    def test_timezone_in_prompt(self, profile) -> None:
        profile.set("timezone", "Europe/Warsaw")
        result = profile.for_prompt()
        assert "Timezone: Europe/Warsaw" in result
