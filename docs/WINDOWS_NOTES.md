# Windows Notes — UAC, UIPI, and Antivirus Considerations

JARVIS runs as a regular (non-elevated) Windows process.  Several tools
interact with the OS at a level where Windows security mechanisms may block
the operation.  This document explains what to expect, which commands
require elevation, and how to work around common issues.

---

## 1. UAC — User Account Control

UAC gates access to **privileged resources** (system-wide registry keys,
protected directories, service management, etc.).  When JARVIS runs without
elevation, any `subprocess.run(...)` or `winreg` call that touches a
protected resource raises either:

| Exception               | How it surfaces                        |
|-------------------------|----------------------------------------|
| `PermissionError`       | Python built-in — clear "access denied"|
| `OSError(winerror=5)`   | Win32 `ERROR_ACCESS_DENIED`            |
| stderr "Access is denied" | Command completed but wrote to stderr |

### Commands that require elevation

| Tool              | Operations needing admin                            |
|-------------------|-----------------------------------------------------|
| `cli_runner`      | `netstat -b`, any command touching `C:\Windows\*`   |
| `power_control`   | `shutdown /s`, `shutdown /r`, hibernate via `PowrProf` on some editions |
| `startup_manager` | Writing to `HKLM\...\Run` (machine-wide startup)   |
| `disk_cleaner`    | Cleaning `%SystemRoot%\Temp`, protected temp dirs   |
| `process_monitor` | Querying full details of SYSTEM/Protected processes |
| `close_app`       | Killing elevated / SYSTEM-owned processes           |

### What JARVIS does about it

Every tool listed above wraps its `execute()` method with:

```python
except PermissionError:
    return ToolResult(success=False, error=_UAC_MSG)
except OSError as e:
    if getattr(e, "winerror", None) == 5:
        return ToolResult(success=False, error=_UAC_MSG)
    ...
```

The user sees a clear message:
> *"I can't do that without administrator privileges, sir.
> Try running JARVIS as admin, or do it manually."*

No stack trace, no confusing internal error.

### Recommendation for users

If you regularly need power-control or startup-management features:

1. **Right-click → Run as administrator** when launching JARVIS.
2. Or create a shortcut with "Run as administrator" checked in
   *Properties → Advanced*.
3. On corporate machines where admin access is unavailable, use the
   read-only variants of the tools (e.g. `list_startup` still works
   without elevation for HKCU keys).

> ⚠️  Running as admin grants JARVIS full access to the system.
> Only do this if you trust the environment and understand the implications.

---

## 2. UIPI — User Interface Privilege Isolation

UIPI prevents a lower-integrity process from sending window messages
(e.g. `WM_CLOSE`, `WM_KEYDOWN`) to a higher-integrity process.

### Impact on JARVIS

| Scenario                                      | Effect                                  |
|-----------------------------------------------|-----------------------------------------|
| `close_app` targeting an elevated application | `terminate()` / `kill()` may fail with `AccessDenied` |
| Focus stealing / `SetForegroundWindow()`      | Blocked if target window is elevated    |
| Sending keystrokes to an elevated window      | Silently dropped by the OS              |

### How to recognize it

- `psutil.AccessDenied` when trying to `terminate()` an elevated process.
- `SetForegroundWindow()` returns `0` (failure) — no exception, just no
  effect.
- Keystrokes sent via `SendInput` / `pyautogui` to an elevated notepad,
  registry editor, etc. are silently swallowed.

### Recommendation

- If the user asks JARVIS to close an elevated app, JARVIS will report
  *"could not close <app>"* — the user needs to close it manually or
  run JARVIS as admin.
- Focus-stealing limitations are a Windows design decision and cannot be
  bypassed without running at the same (or higher) integrity level.

---

## 3. Antivirus / EDR — What Might Get Blocked

Modern antivirus and endpoint detection (Defender, CrowdStrike, Carbon
Black, etc.) may flag or block certain JARVIS operations:

| Operation                     | Why AV might flag it                       |
|-------------------------------|--------------------------------------------|
| `subprocess.run("shutdown")` | Suspicious process spawning a system command |
| `ctypes.windll.PowrProf.*`  | Direct Win32 API calls for power management |
| `winreg.SetValueEx()`        | Writing to `Run` keys (persistence technique) |
| `psutil.Process.kill()`      | Process termination                        |
| `os.remove()` in `%TEMP%`    | Bulk file deletion                         |
| Python process reading other processes' memory info | Process enumeration |

### Common symptoms

- **Quarantine**: AV moves `jarvis.exe` / `python.exe` to quarantine.
  Fix: Add an exclusion for the JARVIS directory.
- **Real-time block**: Operation silently fails or raises `PermissionError`
  even when running as admin.
  Fix: Check AV event logs, add a process exclusion.
- **Behavior alert**: AV logs "suspicious persistence" when
  `add_to_startup` writes a `Run` key.
  Fix: Whitelist the JARVIS process in AV behavioral analysis.

### Recommendation for users

1. **Add JARVIS's installation directory** to your antivirus exclusion list
   (both file-scan and behavior-monitor exclusions).
2. If on a corporate machine with centralized AV policy, ask your IT
   department to whitelist the JARVIS process.
3. **Do NOT disable your antivirus entirely** — just add targeted
   exclusions.

---

## 4. Summary — Quick Reference

| Concern      | What breaks                          | Fix                            |
|--------------|--------------------------------------|--------------------------------|
| UAC          | Admin-only commands/registry writes  | Run JARVIS as admin            |
| UIPI         | Close/focus elevated windows         | Run JARVIS as admin            |
| Antivirus    | Startup keys, process kill, temp cleanup | Add AV exclusion for JARVIS |
| Corporate IT | Group Policy may restrict all of above | Talk to IT / use read-only tools |

---

*Last updated: 2025-01-20 — T6.3 UAC/UIPI awareness*
