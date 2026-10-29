"""Network tools — IP info, speed test, network interfaces, ping.

Uses psutil for interface info, httpx for public IP and speed test,
and subprocess for ping.
"""

from __future__ import annotations

import asyncio
import logging
import platform
import subprocess
import time
from typing import ClassVar

from pydantic import BaseModel, Field

from jarvis.tools.registry import EmptyArgs, ToolResult, VoicePattern

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Lazy imports
# ---------------------------------------------------------------------------

def _get_psutil():
    """Lazy import psutil."""
    try:
        import psutil
        return psutil
    except ImportError:
        return None


def _get_httpx():
    """Lazy import httpx."""
    try:
        import httpx
        return httpx
    except ImportError:
        return None


# ---------------------------------------------------------------------------
# Backend helpers
# ---------------------------------------------------------------------------

def _get_local_ip() -> str | None:
    """Get the primary local IP address."""
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(1)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return None


def _get_public_ip() -> str | None:
    """Get public IP via httpbin.org or ipify.org."""
    httpx = _get_httpx()
    if httpx is None:
        return None

    for url in ("https://api.ipify.org?format=json", "https://httpbin.org/ip"):
        try:
            resp = httpx.get(url, timeout=5)
            data = resp.json()
            return data.get("ip") or data.get("origin")
        except Exception:
            continue
    return None


def _my_ip_impl() -> dict:
    """Get local and public IP."""
    return {
        "local_ip": _get_local_ip(),
        "public_ip": _get_public_ip(),
    }


def _speed_test_impl() -> dict:
    """Simple download speed test — download a known file and measure."""
    httpx = _get_httpx()
    if httpx is None:
        raise NotImplementedError("Speed test requires httpx. Install: pip install httpx")

    # Use Cloudflare's speed test endpoint (~10MB)
    test_urls = [
        ("https://speed.cloudflare.com/__down?bytes=10000000", 10_000_000),
        ("https://proof.ovh.net/files/1Mb.dat", 1_000_000),
    ]

    for url, _expected_size in test_urls:
        try:
            start = time.monotonic()
            resp = httpx.get(url, timeout=30, follow_redirects=True)
            elapsed = time.monotonic() - start

            downloaded = len(resp.content)
            speed_mbps = (downloaded * 8) / (elapsed * 1_000_000)
            speed_mbytes = downloaded / (elapsed * 1_000_000)

            return {
                "download_speed_mbps": round(speed_mbps, 2),
                "download_speed_mb_s": round(speed_mbytes, 2),
                "downloaded_bytes": downloaded,
                "elapsed_seconds": round(elapsed, 2),
            }
        except Exception:
            continue

    raise NotImplementedError("Speed test servers are unreachable.")


def _network_info_impl() -> list[dict]:
    """List network interfaces with addresses."""
    psutil = _get_psutil()
    if psutil is None:
        raise NotImplementedError(
            "Network info requires psutil. Install: pip install psutil"
        )

    interfaces: list[dict] = []
    addrs = psutil.net_if_addrs()
    stats = psutil.net_if_stats()

    for iface_name, addr_list in addrs.items():
        iface_stat = stats.get(iface_name)
        is_up = iface_stat.isup if iface_stat else False
        speed = iface_stat.speed if iface_stat else 0

        ipv4 = None
        ipv6 = None
        mac = None
        for addr in addr_list:
            import socket
            if addr.family == socket.AF_INET:
                ipv4 = addr.address
            elif addr.family == socket.AF_INET6:
                ipv6 = addr.address
            elif addr.family == psutil.AF_LINK:
                mac = addr.address

        interfaces.append({
            "name": iface_name,
            "is_up": is_up,
            "speed_mbps": speed,
            "ipv4": ipv4,
            "ipv6": ipv6,
            "mac": mac,
        })

    return interfaces


def _ping_impl(host: str, count: int = 4) -> str:
    """Ping a host and return the output."""
    param = "-n" if platform.system().lower() == "windows" else "-c"
    try:
        result = subprocess.run(
            ["ping", param, str(count), host],
            capture_output=True,
            text=True,
            timeout=15,
        )
        return result.stdout.strip() or result.stderr.strip()
    except FileNotFoundError:
        raise NotImplementedError("ping command not found.") from None
    except subprocess.TimeoutExpired:
        return f"Ping to {host} timed out after 15 seconds."


# ---------------------------------------------------------------------------
# Args models
# ---------------------------------------------------------------------------

class PingArgs(BaseModel):
    host: str = Field(
        default="google.com",
        description="Host to ping (domain or IP).",
    )
    count: int = Field(
        default=4, ge=1, le=20,
        description="Number of ping packets (1-20).",
    )


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

class MyIpTool:
    """Show local and public IP addresses."""

    name: str = "my_ip"
    description: str = (
        "Show the local (LAN) and public (WAN) IP addresses. "
        "(RU: какой у меня айпи / UK: який у мене айпі)"
    )
    args_schema = EmptyArgs
    requires_confirmation: bool = False
    voice_patterns: ClassVar[tuple[VoicePattern, ...]] = (
        # EN
        VoicePattern(
            regex=r"^(?:what'?s?\s+)?my\s+ip(?:\s+address)?$",
            priority=200,
        ),
        # RU
        VoicePattern(
            regex=r"^какой\s+(?:у\s+меня\s+)?(?:айпи|ip)(?:\s+адрес)?$",
            priority=200,
        ),
        # UK
        VoicePattern(
            regex=r"^який\s+(?:у\s+мене\s+)?(?:айпі|ip)(?:\s+адреса?)?$",
            priority=200,
        ),
    )

    async def execute(self, args: EmptyArgs) -> ToolResult:
        try:
            info = await asyncio.to_thread(_my_ip_impl)
        except Exception as e:
            log.exception("my_ip failed")
            return ToolResult(success=False, error=f"Failed to get IP: {e}")

        lines = ["Your IP addresses, sir:"]
        if info.get("local_ip"):
            lines.append(f"  • Local:  {info['local_ip']}")
        if info.get("public_ip"):
            lines.append(f"  • Public: {info['public_ip']}")
        if not info.get("local_ip") and not info.get("public_ip"):
            lines.append("  Could not determine IP addresses.")

        return ToolResult(success=True, output="\n".join(lines))


class SpeedTestTool:
    """Run a simple internet speed test."""

    name: str = "speed_test"
    description: str = (
        "Run a download speed test to measure internet speed. "
        "(RU: скорость интернета / UK: швидкість інтернету)"
    )
    args_schema = EmptyArgs
    requires_confirmation: bool = False
    voice_patterns: ClassVar[tuple[VoicePattern, ...]] = (
        # EN
        VoicePattern(
            regex=r"^(?:internet\s+)?speed(?:\s+test)?$",
            priority=200,
        ),
        # RU
        VoicePattern(
            regex=r"^скорость\s+интернета$",
            priority=200,
        ),
        # UK
        VoicePattern(
            regex=r"^швидкість\s+інтернету$",
            priority=200,
        ),
    )

    async def execute(self, args: EmptyArgs) -> ToolResult:
        try:
            result = await asyncio.to_thread(_speed_test_impl)
        except NotImplementedError as e:
            return ToolResult(success=False, error=str(e))
        except Exception as e:
            log.exception("speed_test failed")
            return ToolResult(success=False, error=f"Speed test failed: {e}")

        return ToolResult(
            success=True,
            output=(
                f"Download speed: {result['download_speed_mbps']} Mbps "
                f"({result['download_speed_mb_s']} MB/s), sir. "
                f"Downloaded {result['downloaded_bytes']} bytes "
                f"in {result['elapsed_seconds']}s."
            ),
        )


class NetworkInfoTool:
    """Show network interfaces and their addresses."""

    name: str = "network_info"
    description: str = (
        "List network interfaces with IP addresses, MAC, and link status. "
        "Also supports pinging a host. "
        "(RU: информация о сети, пинг / UK: інформація про мережу, пінг)"
    )
    args_schema = PingArgs
    requires_confirmation: bool = False
    voice_patterns: ClassVar[tuple[VoicePattern, ...]] = (
        # EN: "network info"
        VoicePattern(
            regex=r"^network\s+(?:info|information|interfaces?)$",
            priority=200,
        ),
        # EN: "ping google"
        VoicePattern(
            regex=r"^ping\s+(.+)$",
            priority=200,
            args=lambda m: {"host": m.group(1)},
        ),
        # RU: "информация о сети"
        VoicePattern(
            regex=r"^информация\s+о\s+сети$",
            priority=200,
        ),
        # RU: "пинг гугла"
        VoicePattern(
            regex=r"^пинг\s+(.+)$",
            priority=200,
            args=lambda m: {"host": m.group(1)},
        ),
        # UK: "інформація про мережу"
        VoicePattern(
            regex=r"^інформація\s+про\s+мережу$",
            priority=200,
        ),
        # UK: "пінг гугла"
        VoicePattern(
            regex=r"^пінг\s+(.+)$",
            priority=200,
            args=lambda m: {"host": m.group(1)},
        ),
    )

    async def execute(self, args: PingArgs) -> ToolResult:
        # If a specific host is given (not default), do a ping
        if args.host != "google.com" or True:
            # Check if this is a ping request vs info request
            pass

        try:
            # Get network interfaces
            try:
                interfaces = await asyncio.to_thread(_network_info_impl)
            except NotImplementedError:
                interfaces = []

            lines = []

            if interfaces:
                active = [i for i in interfaces if i["is_up"]]
                lines.append(
                    f"Network interfaces ({len(active)} active of {len(interfaces)} total):"
                )
                for iface in interfaces:
                    if not iface["is_up"]:
                        continue
                    status = "UP" if iface["is_up"] else "DOWN"
                    line = f"  • {iface['name']} [{status}]"
                    if iface.get("speed_mbps"):
                        line += f" {iface['speed_mbps']} Mbps"
                    if iface.get("ipv4"):
                        line += f"\n    IPv4: {iface['ipv4']}"
                    if iface.get("mac"):
                        line += f"  MAC: {iface['mac']}"
                    lines.append(line)

            # Ping the host
            ping_output = await asyncio.to_thread(_ping_impl, args.host, args.count)
            lines.append(f"\nPing {args.host}:")
            lines.append(ping_output)

            return ToolResult(
                success=True, output="\n".join(lines) + "\n\nThat's your network status, sir."
            )

        except NotImplementedError as e:
            return ToolResult(success=False, error=str(e))
        except Exception as e:
            log.exception("network_info failed")
            return ToolResult(success=False, error=f"Network info failed: {e}")
