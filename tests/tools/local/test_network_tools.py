"""Tests for jarvis.tools.local.network_tools.

Network access (httpx, subprocess) is mocked — no real network calls.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from jarvis.tools.local.network_tools import (
    MyIpTool,
    NetworkInfoTool,
    PingArgs,
    SpeedTestTool,
)
from jarvis.tools.registry import EmptyArgs


# --- MyIpTool -------------------------------------------------------------

async def test_my_ip_success():
    mock_info = {"local_ip": "192.168.1.100", "public_ip": "203.0.113.42"}
    with patch(
        "jarvis.tools.local.network_tools._my_ip_impl",
        return_value=mock_info,
    ):
        result = await MyIpTool().execute(EmptyArgs())
    assert result.success
    assert "192.168.1.100" in (result.output or "")
    assert "203.0.113.42" in (result.output or "")


async def test_my_ip_no_public():
    mock_info = {"local_ip": "192.168.1.100", "public_ip": None}
    with patch(
        "jarvis.tools.local.network_tools._my_ip_impl",
        return_value=mock_info,
    ):
        result = await MyIpTool().execute(EmptyArgs())
    assert result.success
    assert "192.168.1.100" in (result.output or "")


async def test_my_ip_none():
    mock_info = {"local_ip": None, "public_ip": None}
    with patch(
        "jarvis.tools.local.network_tools._my_ip_impl",
        return_value=mock_info,
    ):
        result = await MyIpTool().execute(EmptyArgs())
    assert result.success
    assert "could not determine" in (result.output or "").lower()


# --- SpeedTestTool --------------------------------------------------------

async def test_speed_test_success():
    mock_result = {
        "download_speed_mbps": 85.5,
        "download_speed_mb_s": 10.7,
        "downloaded_bytes": 10_000_000,
        "elapsed_seconds": 0.94,
    }
    with patch(
        "jarvis.tools.local.network_tools._speed_test_impl",
        return_value=mock_result,
    ):
        result = await SpeedTestTool().execute(EmptyArgs())
    assert result.success
    assert "85.5" in (result.output or "")
    assert "Mbps" in (result.output or "")


async def test_speed_test_not_implemented():
    with patch(
        "jarvis.tools.local.network_tools._speed_test_impl",
        side_effect=NotImplementedError("httpx required"),
    ):
        result = await SpeedTestTool().execute(EmptyArgs())
    assert not result.success
    assert "httpx" in (result.error or "").lower()


# --- NetworkInfoTool ------------------------------------------------------

async def test_network_info_success():
    mock_interfaces = [
        {
            "name": "Ethernet",
            "is_up": True,
            "speed_mbps": 1000,
            "ipv4": "192.168.1.100",
            "ipv6": "fe80::1",
            "mac": "AA:BB:CC:DD:EE:FF",
        },
    ]
    with patch(
        "jarvis.tools.local.network_tools._network_info_impl",
        return_value=mock_interfaces,
    ):
        with patch(
            "jarvis.tools.local.network_tools._ping_impl",
            return_value="Reply from 8.8.8.8: bytes=32 time=10ms TTL=118",
        ):
            result = await NetworkInfoTool().execute(
                PingArgs(host="google.com", count=4),
            )
    assert result.success
    assert "Ethernet" in (result.output or "")
    assert "192.168.1.100" in (result.output or "")


async def test_network_info_ping_timeout():
    with patch(
        "jarvis.tools.local.network_tools._network_info_impl",
        return_value=[],
    ):
        with patch(
            "jarvis.tools.local.network_tools._ping_impl",
            return_value="Ping to example.com timed out after 15 seconds.",
        ):
            result = await NetworkInfoTool().execute(
                PingArgs(host="example.com"),
            )
    assert result.success
    assert "timed out" in (result.output or "")


def test_requires_confirmation_false():
    assert MyIpTool().requires_confirmation is False
    assert SpeedTestTool().requires_confirmation is False
    assert NetworkInfoTool().requires_confirmation is False


def test_voice_patterns_exist():
    assert len(MyIpTool.voice_patterns) >= 3
    assert len(SpeedTestTool.voice_patterns) >= 3
    assert len(NetworkInfoTool.voice_patterns) >= 6


def test_ping_args_bounds():
    PingArgs(host="google.com", count=1)  # OK
    PingArgs(host="google.com", count=20)  # OK
    with pytest.raises(Exception):
        PingArgs(host="google.com", count=0)
    with pytest.raises(Exception):
        PingArgs(host="google.com", count=21)
