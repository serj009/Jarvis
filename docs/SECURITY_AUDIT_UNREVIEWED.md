# Security & Reliability Audit — previously-unreviewed modules (Этап 12)

> REPORT ONLY. No code is changed by this document. Findings are listed with
> a severity and a concrete code location. Items 1–2 (MCP, Vault) were
> already hardened in earlier stages of this audit — their status is noted.

Severity scale: **High** (exploitable / data loss), **Medium** (needs a
precondition or is defence-in-depth), **Low** (hygiene / hardening).

---

## 1. MCP client (`jarvis/tools/mcp_client.py`) — ALREADY HARDENED

Hardened in commit `a81fcfd` (this audit). Residual notes only.

| # | Severity | Finding | Location |
|---|---|---|---|
| 1.1 | ~~High~~ FIXED | File-sourced Trayce token was sent to ANY configured URL. Now gated to local hosts via `is_local_url`. | `resolve_trayce_endpoint` |
| 1.2 | ~~Medium~~ FIXED | Non-local servers ran tools without confirmation. Now forced `requires_confirmation=True`. | `add_server` |
| 1.3 | ~~Medium~~ FIXED | Tool descriptions reached the LLM verbatim (poisoning). Now cleaned + length-capped. | `_clean_tool_description` |
| 1.4 | ~~Low~~ FIXED | "Rug pull" (server swaps its tool set between connects) undetected. Now fingerprinted + logged. | `_check_tool_set_change` |
| 1.5 | **Low (open)** | Call results are cleaned by Layer 3 (`clean_external_text` in `_translate_call_result`), but a malicious server could still return a huge payload before truncation in the tool output. Consider a byte cap on `joined`. | `_translate_call_result` |

---

## 2. Vault (`jarvis/security/password_vault.py`, `tools/local/vault_manager.py`) — ALREADY HARDENED

Hardened in commit `c250de7` (this audit). Residual notes only.

| # | Severity | Finding | Location |
|---|---|---|---|
| 2.1 | ~~High~~ FIXED | Silent crypto downgrade (scrypt / HMAC-XOR) produced unreadable vaults. Now Argon2id+AES-256-GCM only, fail-closed. | `_derive_key`, `_aes_encrypt` |
| 2.2 | ~~Medium~~ FIXED | Error modes were indistinguishable. Now 4 distinct error types. | error taxonomy |
| 2.3 | ~~Medium~~ FIXED | Unicode credential tests failed (encoding). Green after the rewrite. | `test_unicode_credentials` |
| 2.4 | **Low (open)** | `get_credential` returns the plaintext password in a `VaultEntry`. The docstring warns callers never to log it or put it in LLM context, but nothing enforces it. Consider a `SecretStr`-style wrapper that refuses `__repr__`/`__str__`. | `VaultEntry.password` |
| 2.5 | **Low (open)** | `_secure_zero` is best-effort (Python can keep copies). Documented, unavoidable in pure Python; noted for completeness. | `_secure_zero` |

---

## 3. Browser automation (`jarvis/browser/browser_controller.py`)

**ПРОВЕРЬ — РАСХОЖДЕНИЕ С ОПИСАНИЕМ:** the task calls this "Playwright"
automation. In fact the project uses **Selenium** (17 files reference
selenium; `playwright` appears only in one plugin, `integrations/plugins/
whatsapp.py`). The findings below are for the Selenium controller.

| # | Severity | Finding | Location |
|---|---|---|---|
| 3.1 | **High** | `execute_js(script)` runs ARBITRARY JavaScript in the live browser via `driver.execute_script`. If `script` is ever derived from LLM output or page content, this is RCE-in-page (cookie theft, DOM exfiltration). It is a registered tool surface — must be gated like `code_runner` (DESTRUCTIVE + no LLM-initiated execution). NOT currently in `EXEC_TOOLS`. | `execute_js`, line ~258 |
| 3.2 | **Medium** | `open_tab` builds a JS `window.open` call via `execute_script` with the URL interpolated (line ~130). A URL containing a quote/`</script>`-style payload could break out of the JS string. Prefer the Selenium tab API over string-built JS, or hard-validate the URL. | `open_tab`, line ~130 |
| 3.3 | **Medium** | `get_page_text` / `get_page_source` pull untrusted page content. These feed the LLM context; they should pass through the Layer-3 `clean_external_text` seam (as `content_reader` now does) — verify the call path does. | lines ~231, ~247 |
| 3.4 | **Low** | Auto-login / credential fill path: confirm vault plaintext passwords (see 2.4) are typed into forms and never logged or screenshotted. `get_page_source` after a login could capture a session token into memory/logs. | auto_login + browser |

---

## 4. Integrations (`jarvis/integrations/`) — OAuth storage & scopes

Credentials use `EncryptedJsonStore` (Windows DPAPI at rest) — a deliberate
choice over `PasswordVault` because background startup can't prompt for a
master password (documented in `auth.py` header). Reasonable on Windows.

| # | Severity | Finding | Location |
|---|---|---|---|
| 4.1 | **Medium** | OAuth2 `state` parameter is NOT verified. `run_callback_server` captures only `?code=`; it never checks `state` against the value put in `build_auth_url`. This is the classic OAuth CSRF gap — an attacker who can hit the localhost callback could inject their own `code`. Fix: generate a random `state`, pass it through, and reject a callback whose `state` doesn't match. | `run_callback_server` line ~197; `build_auth_url` line ~102 |
| 4.2 | **Medium** | On non-Windows, `EncryptedJsonStore` falls back to PLAINTEXT JSON (documented DPAPI behaviour). OAuth refresh tokens would sit in cleartext on Linux/macOS. Acceptable only if the product is Windows-only; otherwise refresh tokens need another at-rest mechanism off-Windows. | `encrypted_store.py` fallback |
| 4.3 | **Low** | `run_callback_server` binds `localhost:8765` with a stdlib `HTTPServer` and serves exactly one request — good. But there's no check that the port isn't already taken by another process that could capture the code. Low likelihood, worth a bind-failure log. | `run_callback_server` line ~211 |
| 4.4 | **Low** | Scopes aren't surfaced to the user at authorise time. The `build_auth_url` caller sets scope; consider showing the requested scopes in the consent UI so an over-broad plugin scope is visible. | `build_auth_url` |

---

## 5. Audio pipeline (`jarvis/audio/`) — threads, asyncio races, errors

The pipeline runs an asyncio loop on a dedicated thread; TTS uses a PortAudio
realtime callback. Этап 9 already logged the swallowed exceptions here.

| # | Severity | Finding | Location |
|---|---|---|---|
| 5.1 | **Low** | The realtime PortAudio callback (`tts.py` ~944) must never block or raise; its silence-fill `except` is intentionally silent (now commented + `# noqa: S110`). Correct, but any future I/O added there would cause xruns. Keep a guard/comment. | `tts.py` ~944 |
| 5.2 | **Low** | Queue hand-off between the audio thread and Qt uses `QMetaObject.invokeMethod` / scheduled callbacks. The boundaries are correct but implicit; the Этап 11 decomposition proposes an explicit `UiCallbacks`/`AudioCommands` seam which would make the thread rules enforceable rather than prose. | pipeline ↔ app glue |
| 5.3 | **Low** | `CancelledError` handlers (pipeline 286/381/766, resource_monitor 152) correctly swallow cancellation on shutdown. Verified semantically correct in Этап 9. No action. | pipeline.py |
| 5.4 | **Info** | STT confidence formula was softened for RU/UK (`exp(avg_logprob/2)`) in a prior session. Not a security issue; flagged only so a future tightening doesn't silently re-introduce dropped Cyrillic utterances. | stt.py |

---

## 6. Migrations (`jarvis/core/migrations.py`) — rollback, integrity

`migrate(data: dict)` operates on an **in-memory dict**, stepping schema
versions one at a time with strict validation (each migration must bump
exactly +1, newer-than-supported is rejected). The on-disk config is only
rewritten AFTER a successful in-memory migration, so a mid-migration crash
does NOT leave a half-written file.

| # | Severity | Finding | Location |
|---|---|---|---|
| 6.1 | **Medium** | No backup of the pre-migration config is taken before the migrated dict is written back. If a migration is buggy (not crashing, but producing wrong data) the original is lost. Recommend writing `<config>.vN.bak` before persisting the migrated config (mirror the vault's `.v1.bak` approach from Task 9A). | config save path (caller of `migrate`) |
| 6.2 | **Low** | Migrations have no transaction wrapper, but since they're pure dict transforms with +1 validation and atomic file write, this is acceptable. The risk is logic bugs in a migration, not partial writes. | `migrate()` |
| 6.3 | **Low** | `migrate()` mutates and returns the SAME dict object (documented). A caller that kept a reference to the pre-migration dict would see it change under them. Minor; a deep-copy at entry would be safer. | `migrate()` line 906 |
| 6.4 | **Info** | Migrations were extracted from `config.py` into `migrations.py` in a prior session; the earlier NameError bug (migration functions defined after the MIGRATIONS dict) is fixed. No regression found. | module structure |

---

## Summary — highest-priority open items (not fixed by this report)

1. **3.1 (High)** — `browser_controller.execute_js` runs arbitrary JS and is
   not gated like `code_runner`; add it to `EXEC_TOOLS` and block
   LLM-initiated calls.
2. **4.1 (Medium)** — OAuth `state` not verified (CSRF).
3. **6.1 (Medium)** — no config backup before migration write.
4. **3.3 (Medium)** — verify browser page-text goes through the Layer-3
   sanitizer before reaching the LLM.
5. **4.2 (Medium)** — OAuth refresh tokens are plaintext off-Windows.

These are findings, not changes. Fixing them would each be a separate
scoped task with its own tests, following the one-stage-one-commit rule.
