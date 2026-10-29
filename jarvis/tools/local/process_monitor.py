"""Process monitor — inspect running processes, find heavy consumers.

Uses psutil (lazy import) with a tasklist fallback for basic queries.
"""

from __future__ import annotations

import asyncio
import logging
from typing import ClassVar

from pydantic import BaseModel, Field

from jarvis.tools.registry import ToolResult, VoicePattern

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# UAC / elevation helpers
# ---------------------------------------------------------------------------

_UAC_MSG = (
    "I can't do that without administrator privileges, sir. "
    "Try running JARVIS as admin, or do it manually."
)


def _is_access_denied(exc: OSError) -> bool:
    """Return True if *exc* is a Windows ERROR_ACCESS_DENIED (winerror 5)."""
    return getattr(exc, "winerror", None) == 5



def _get_psutil():
    """Lazy import psutil."""
    try:
        import psutil
        return psutil
    except ImportError:
        return None


# ---------------------------------------------------------------------------
# Backend
# ---------------------------------------------------------------------------

def _get_process_info(name: str) -> dict:
    """Get info about processes matching `name`."""
    psutil = _get_psutil()
    if psutil is not None:
        matches = []
        name_lower = name.lower()
        for proc in psutil.process_iter(["name", "pid", "memory_info", "cpu_percent"]):
            try:
                pname = proc.info.get("name", "")
                if name_lower in pname.lower():
                    mem = proc.info.get("memory_info")
                    mem_mb = round(mem.rss / (1024 * 1024), 1) if mem else 0
                    matches.append({
                        "name": pname,
                        "pid": proc.info.get("pid"),
                        "memory_mb": mem_mb,
                        "cpu_percent": proc.cpu_percent(interval=0.1),
                    })
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        if not matches:
            return {"success": False, "error": f"Process '{name}' not found."}

        # Aggregate for multi-process apps (Chrome, etc.)
        total_mem = sum(m["memory_mb"] for m in matches)
        total_cpu = sum(m["cpu_percent"] for m in matches)
        return {
            "success": True,
            "name": name,
            "process_count": len(matches),
            "total_memory_mb": round(total_mem, 1),
            "total_cpu_percent": round(total_cpu, 1),
            "processes": matches[:10],
        }

    # Fallback: tasklist
    try:
        import subprocess
        result = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {name}*", "/FO", "CSV", "/V"],
            capture_output=True, text=True, timeout=10,
        )
        output = result.stdout.strip()
        if not output or "No tasks" in output:
            return {"success": False, "error": f"Process '{name}' not found."}
        return {"success": True, "name": name, "raw": output[:2000]}
    except Exception as e:
        return {"success": False, "error": str(e)}


def _list_heavy_processes(top_n: int = 10) -> dict:
    """List the heaviest processes by memory usage."""
    psutil = _get_psutil()
    if psutil is None:
        return {"success": False, "error": "psutil not installed."}

    processes = []
    for proc in psutil.process_iter(["name", "pid", "memory_info"]):
        try:
            mem = proc.info.get("memory_info")
            if mem:
                processes.append({
                    "name": proc.info.get("name", "?"),
                    "pid": proc.info.get("pid"),
                    "memory_mb": round(mem.rss / (1024 * 1024), 1),
                })
        except Exception:
            continue

    processes.sort(key=lambda x: x["memory_mb"], reverse=True)
    return {
        "success": True,
        "processes": processes[:top_n],
        "total_count": len(processes),
    }


def _wait_for_exit(name: str, timeout: int = 600, poll_sec: float = 2.0) -> dict:
    """Block until a process exits (or timeout)."""
    import time

    psutil = _get_psutil()
    if psutil is None:
        return {"success": False, "error": "psutil not installed."}

    name_lower = name.lower()
    deadline = time.time() + timeout

    while time.time() < deadline:
        found = False
        for proc in psutil.process_iter(["name"]):
            try:
                if name_lower in (proc.info.get("name") or "").lower():
                    found = True
                    break
            except Exception:
                continue
        if not found:
            return {"success": True, "message": f"Process '{name}' has exited."}
        time.sleep(poll_sec)

    return {"success": False, "error": f"Timeout: '{name}' still running after {timeout}s."}


# ---------------------------------------------------------------------------
# Args models
# ---------------------------------------------------------------------------

class ProcessInfoArgs(BaseModel):
    name: str = Field(
        description="Process name to inspect (e.g. chrome, discord, setup)."
    )


class HeavyProcessesArgs(BaseModel):
    top_n: int = Field(
        default=10, ge=1, le=50,
        description="Number of top processes to show (1–50).",
    )


class WaitForExitArgs(BaseModel):
    name: str = Field(description="Process name to wait for.")
    timeout: int = Field(
        default=600, ge=10, le=3600,
        description="Maximum wait time in seconds (10–3600).",
    )


# ---------------------------------------------------------------------------
# Tool classes
# ---------------------------------------------------------------------------

class ProcessInfoTool:
    name: str = "process_info"
    description: str = (
        "Get information about a running process — memory usage, CPU %, "
        "process count. Works with multi-process apps (Chrome, etc.). "
        "(RU: сколько памяти ест, информация о процессе / "
        "UK: скільки пам'яті їсть, інформація про процес)"
    )
    args_schema = ProcessInfoArgs
    requires_confirmation: bool = False
    voice_patterns: ClassVar[tuple[VoicePattern, ...]] = (
        # EN: "how much memory does chrome use"
        VoicePattern(
            regex=r"^how\s+much\s+(?:memory|ram)\s+(?:does|is)\s+(.+)\s+(?:use|using|eat|eating)$",
            priority=200,
            args=lambda m: {"name": m.group(1)},
        ),
        # RU: "сколько памяти ест хром", "сколько жрёт дискорд"
        VoicePattern(
            regex=r"^сколько\s+(?:памяти|рам|оперативки)\s+(?:ест|жрёт|использует)\s+(.+)$",
            priority=200,
            args=lambda m: {"name": m.group(1)},
        ),
        # UK: "скільки пам'яті їсть хром"
        VoicePattern(
            regex=r"^скільки\s+(?:пам'яті|рам|оперативки)\s+(?:їсть|використовує)\s+(.+)$",
            priority=200,
            args=lambda m: {"name": m.group(1)},
        ),
    )

    async def execute(self, args: ProcessInfoArgs) -> ToolResult:
        try:
            result = await asyncio.to_thread(_get_process_info, args.name)
        except PermissionError:
            return ToolResult(success=False, error=_UAC_MSG)
        except OSError as e:
            if _is_access_denied(e):
                return ToolResult(success=False, error=_UAC_MSG)
            return ToolResult(success=False, error=f"Process query failed: {e}")
        except Exception as e:
            return ToolResult(success=False, error=f"Process query failed: {e}")

        if not result.get("success"):
            return ToolResult(success=False, error=result.get("error", "Not found."))

        count = result.get("process_count", 1)
        mem = result.get("total_memory_mb", 0)
        cpu = result.get("total_cpu_percent", 0)

        msg = (
            f"{args.name}: {count} process(es), "
            f"{mem} MB RAM, {cpu}% CPU, sir."
        )
        return ToolResult(success=True, output=msg)


class HeavyProcessesTool:
    name: str = "heavy_processes"
    description: str = (
        "Lists the processes using the most memory (RAM). "
        "(RU: какие процессы жрут больше всего RAM / "
        "UK: які процеси їдять найбільше RAM)"
    )
    args_schema = HeavyProcessesArgs
    requires_confirmation: bool = False
    voice_patterns: ClassVar[tuple[VoicePattern, ...]] = (
        VoicePattern(
            regex=r"^(?:heavy|top)\s+processes$",
            priority=200,
        ),
        VoicePattern(
            regex=r"^(?:какие|что)\s+(?:процессы|приложения)\s+(?:жрут|едят|используют)\s+больше\s+всего",
            priority=200,
        ),
        VoicePattern(
            regex=r"^(?:які|що)\s+(?:процеси|додатки)\s+(?:їдять|використовують)\s+найбільше",
            priority=200,
        ),
    )

    async def execute(self, args: HeavyProcessesArgs) -> ToolResult:
        try:
            result = await asyncio.to_thread(_list_heavy_processes, args.top_n)
        except PermissionError:
            return ToolResult(success=False, error=_UAC_MSG)
        except OSError as e:
            if _is_access_denied(e):
                return ToolResult(success=False, error=_UAC_MSG)
            return ToolResult(success=False, error=f"Failed: {e}")
        except Exception as e:
            return ToolResult(success=False, error=f"Failed: {e}")

        if not result.get("success"):
            return ToolResult(success=False, error=result.get("error", "Failed."))

        procs = result.get("processes", [])
        lines = [f"Top {len(procs)} processes by RAM usage:"]
        for i, p in enumerate(procs, 1):
            lines.append(f"  {i}. {p['name']} — {p['memory_mb']} MB (PID {p['pid']})")

        return ToolResult(success=True, output="\n".join(lines))


class WaitForProcessTool:
    name: str = "wait_for_process"
    description: str = (
        "Waits until a specific process exits (e.g. an installer). "
        "Notifies when done. "
        "(RU: подожди пока закончит, дождись завершения / "
        "UK: зачекай поки закінчить, дочекайся завершення)"
    )
    args_schema = WaitForExitArgs
    requires_confirmation: bool = False
    voice_patterns: ClassVar[tuple[VoicePattern, ...]] = (
        VoicePattern(
            regex=r"^wait\s+(?:for|until)\s+(.+)\s+(?:exits?|finishes?|ends?)$",
            priority=200,
            args=lambda m: {"name": m.group(1)},
        ),
        VoicePattern(
            regex=r"^(?:подожди|дождись)\s+(?:пока|когда)\s+(.+)\s+(?:закончит|завершит)$",
            priority=200,
            args=lambda m: {"name": m.group(1)},
        ),
        VoicePattern(
            regex=r"^(?:зачекай|дочекайся)\s+(?:поки|коли)\s+(.+)\s+(?:закінчить|завершить)$",
            priority=200,
            args=lambda m: {"name": m.group(1)},
        ),
    )

    async def execute(self, args: WaitForExitArgs) -> ToolResult:
        try:
            result = await asyncio.to_thread(
                _wait_for_exit, args.name, args.timeout,
            )
        except PermissionError:
            return ToolResult(success=False, error=_UAC_MSG)
        except OSError as e:
            if _is_access_denied(e):
                return ToolResult(success=False, error=_UAC_MSG)
            return ToolResult(success=False, error=f"Wait failed: {e}")
        except Exception as e:
            return ToolResult(success=False, error=f"Wait failed: {e}")

        if result.get("success"):
            return ToolResult(
                success=True,
                output=f"Process '{args.name}' has finished, sir.",
            )
        return ToolResult(success=False, error=result.get("error", "Wait failed."))
