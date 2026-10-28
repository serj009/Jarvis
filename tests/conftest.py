"""Root test conftest — disable DPAPI encryption for the entire test suite.

Production code encrypts DB columns (``encrypted_db.py``) and JSON files
(``encrypted_store.py``) via Windows DPAPI.  On a Windows development
machine ``dpapi_available()`` returns True, so ``encrypt_value`` /
``decrypt_value`` produce ``dpapi:<base64>`` strings.

That is correct for production but **breaks tests** in three ways:

1. **FTS5 / LIKE search** operates on raw column values — encrypted
   text does not match a plaintext query, so ``MemoryStore.search``
   returns nothing even though the fact was just inserted.
2. **Direct-SQL helpers** used by tests (``_set_tier``, ``_get_tier``,
   etc.) bypass the encryption layer, creating a mix of plaintext and
   ciphertext in the same column.
3. **Tag-based filtering** stores encrypted tags; a plaintext
   ``WHERE tag = ?`` never matches.

Fix: patch ``dpapi_available`` to return ``False`` for *all* tests by
default.  ``encrypt_value`` / ``decrypt_value`` / ``EncryptedJsonStore``
then become transparent pass-throughs (returning the input unchanged),
exactly as they already do on non-Windows hosts.

Tests that **intentionally verify encryption** (e.g.
``tests/security/test_data_encryption.py``) provide their own
``dpapi_mock`` fixture that overrides this autouse fixture back to
``True`` and supplies fake ``_protect`` / ``_unprotect`` stubs.  The
``unittest.mock.patch`` stacking order is deterministic: the inner
(test-level) mock wins while active, and reverts to the outer (conftest)
mock on teardown.

.. seealso::
   ``jarvis/platform/secrets.py``  — ``dpapi_available()``,
   ``jarvis/platform/encrypted_db.py`` — ``encrypt_value`` /
   ``decrypt_value``,
   ``jarvis/platform/encrypted_store.py`` — ``EncryptedJsonStore``.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def _isolate_data_dir(
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> Path:
    """Point Jarvis's data dir at a throwaway temp dir for every test.

    Production data modules resolve their location through
    ``jarvis.paths.user_data_dir()``, whose first rule is the
    ``JARVIS_DATA_DIR`` env var (Task 4+B). Setting it here guarantees no
    test ever reads or writes the user's real ``%APPDATA%\\Jarvis`` /
    ``~/.jarvis`` tree, and that each test starts from an empty data root.

    ``monkeypatch`` reverts the env var on teardown, so the process
    environment is restored. Tests that need a *specific* data dir (or that
    verify the platform-based resolution with JARVIS_DATA_DIR unset) simply
    call ``monkeypatch.setenv`` / ``delenv`` themselves — that runs inside
    the test body and overrides this autouse default.
    """
    data_dir = tmp_path_factory.mktemp("jarvis_data")
    monkeypatch.setenv("JARVIS_DATA_DIR", str(data_dir))
    return data_dir


@pytest.fixture(autouse=True)
def _disable_dpapi_for_tests():
    """Make all DPAPI-based encryption a transparent no-op.

    Every encrypt/decrypt function in ``jarvis.platform`` checks
    ``dpapi_available()`` first; returning ``False`` here causes them
    to return the input unchanged — no encryption, no decryption, no
    Windows API calls.
    """
    with patch("jarvis.platform.secrets.dpapi_available", return_value=False):
        yield


@pytest.fixture(autouse=True)
def _reset_cuda_dll_cache(monkeypatch: pytest.MonkeyPatch):
    """Isolate jarvis.platform.cuda_dlls between tests.

    ``add_cuda_dll_directories`` caches its result for the life of the process
    and prepends the NVIDIA bin dirs to ``os.environ['PATH']``. Both are
    process-global side effects, so without isolation one test's run would
    leak into the next (and the pre-existing on/off-Windows tests, which don't
    know about the cache, would otherwise see a stale result).

    This fixture:
      * snapshots PATH via ``monkeypatch.setenv`` so any PATH mutation a test
        triggers is reverted automatically on teardown, and
      * clears the module cache before AND after each test.

    Import is lazy and tolerant: if the module can't be imported, the fixture
    is a no-op rather than erroring every test.
    """
    monkeypatch.setenv("PATH", os.environ.get("PATH", ""))
    try:
        from jarvis.platform import cuda_dlls
    except ImportError:
        yield
        return
    cuda_dlls._reset_cache_for_tests()
    yield
    cuda_dlls._reset_cache_for_tests()
