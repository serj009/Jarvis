"""Undo for destructive file actions: "верни как было" (M2-4, T12.2).

Only deleting and overwriting can lose data, so only those are kept:

- **delete** (file_manager): the file or folder is not erased but moved
  into the undo store;
- **overwrite** (file_manager move / copy / rename onto an existing
  file): the old file is moved into the store before it is replaced.

"Верни как было" / "undo that" puts the most recent one back. Restoring
an overwrite first moves the newer file into the store too, so the undo
itself loses nothing.

The store is ``<data dir>/undo/``: one folder per entry plus an index.
It keeps at most ``MAX_ENTRIES`` entries for at most ``MAX_AGE_DAYS``;
older ones are deleted for good when a new entry is added.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import ClassVar, Literal

from jarvis.platform.encrypted_store import EncryptedJsonStore
from jarvis.tools.registry import EmptyArgs, ToolResult, VoicePattern
from jarvis.tools.risk import RiskLevel

log = logging.getLogger(__name__)

MAX_ENTRIES = 20
MAX_AGE_DAYS = 7

Action = Literal["delete", "overwrite"]


def _default_root() -> Path:
    from jarvis.paths import data_path

    return data_path("undo")


@dataclass
class UndoEntry:
    id: str
    action: Action
    original: str  # where the file was / is
    stored: str  # where its old version is kept now
    created_at: float

    @property
    def name(self) -> str:
        return Path(self.original).name


class UndoStore:
    """Keeps deleted and overwritten files so they can be put back."""

    def __init__(self, root: Path | None = None) -> None:
        self._root = root
        self._index_store: EncryptedJsonStore | None = None

    # -- storage ---------------------------------------------------------

    @property
    def root(self) -> Path:
        # Resolved on first use, so a tool built at import time follows the
        # data dir the app (or a test) sets later.
        if self._root is None:
            self._root = _default_root()
        return self._root

    def _index(self) -> EncryptedJsonStore:
        if self._index_store is None:
            self._index_store = EncryptedJsonStore(self.root / "index.json")
        return self._index_store

    def entries(self) -> list[UndoEntry]:
        """Newest last."""
        raw = self._index().load(default=[])
        out: list[UndoEntry] = []
        for item in raw if isinstance(raw, list) else []:
            try:
                out.append(UndoEntry(**item))
            except TypeError:
                log.debug("[undo] skipping a malformed entry: %r", item)
        return out

    def _save(self, entries: list[UndoEntry]) -> None:
        self._index().save([asdict(e) for e in entries])

    # -- recording -------------------------------------------------------

    def keep(self, path: Path, action: Action) -> UndoEntry:
        """Move *path* into the store. Raises OSError if it cannot be moved
        (then nothing was deleted or overwritten)."""
        entry_id = uuid.uuid4().hex[:12]
        slot = self.root / entry_id
        slot.mkdir(parents=True, exist_ok=True)
        stored = slot / path.name
        try:
            shutil.move(str(path), str(stored))
        except BaseException:
            shutil.rmtree(slot, ignore_errors=True)
            raise
        entry = UndoEntry(
            id=entry_id, action=action, original=str(path), stored=str(stored),
            created_at=time.time(),
        )
        self._save(self._pruned([*self.entries(), entry]))
        log.info("[undo] kept %s before %s", path, action)
        return entry

    def _pruned(self, entries: list[UndoEntry]) -> list[UndoEntry]:
        cutoff = time.time() - MAX_AGE_DAYS * 86400
        keep = [e for e in entries if e.created_at >= cutoff][-MAX_ENTRIES:]
        kept_ids = {e.id for e in keep}
        for old in entries:
            if old.id not in kept_ids:
                shutil.rmtree(self.root / old.id, ignore_errors=True)
                log.info("[undo] forgot %s (%s)", old.original, old.action)
        return keep

    # -- undo ------------------------------------------------------------

    def undo_last(self) -> tuple[UndoEntry | None, str | None]:
        """Put the newest entry back. Returns (entry, error)."""
        entries = self.entries()
        if not entries:
            return None, "nothing"
        return self.restore(entries[-1].id)

    def restore(self, entry_id: str) -> tuple[UndoEntry | None, str | None]:
        """Put entry *entry_id* back. Returns (entry, error); error is
        "gone" (its copy is missing) or "occupied" (a deleted file's place
        was taken), else None."""
        entries = self.entries()
        entry = next((e for e in entries if e.id == entry_id), None)
        if entry is None:
            return None, "nothing"
        entries = [e for e in entries if e.id != entry_id]
        original = Path(entry.original)
        stored = Path(entry.stored)
        if not stored.exists():
            self._save(entries)
            return entry, "gone"
        if original.exists():
            if entry.action == "delete":
                # Something new took its place; do not touch it.
                return entry, "occupied"
            # Overwrite: keep the newer version too before restoring.
            self.keep(original, "overwrite")
            entries = [e for e in self.entries() if e.id != entry_id]
        original.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(stored), str(original))
        shutil.rmtree(self.root / entry.id, ignore_errors=True)
        self._save(entries)
        log.info("[undo] restored %s (%s)", original, entry.action)
        return entry, None


# -- the voice tool ----------------------------------------------------------

_PHRASES: dict[str, dict[str, str]] = {
    "delete": {
        "ru": "Вернул «{name}» на место, сэр.",
        "uk": "Повернув «{name}» на місце, сер.",
        "en": "Restored \"{name}\", sir.",
    },
    "overwrite": {
        "ru": "Вернул прежнюю версию «{name}», сэр.",
        "uk": "Повернув попередню версію «{name}», сер.",
        "en": "Restored the previous version of \"{name}\", sir.",
    },
    "nothing": {
        "ru": "Нечего возвращать, сэр: удалённых или перезаписанных файлов нет.",
        "uk": "Нічого повертати, сер: видалених чи перезаписаних файлів немає.",
        "en": "There is nothing to undo, sir: no deleted or overwritten files.",
    },
    "occupied": {
        "ru": "Не могу вернуть «{name}», сэр: на его месте уже новый файл.",
        "uk": "Не можу повернути «{name}», сер: на його місці вже новий файл.",
        "en": "I can't restore \"{name}\", sir: a new file is in its place.",
    },
    "gone": {
        "ru": "Копия «{name}» уже не сохранилась, сэр.",
        "uk": "Копія «{name}» вже не збереглася, сер.",
        "en": "The copy of \"{name}\" is no longer kept, sir.",
    },
    "failed": {
        "ru": "Не получилось вернуть «{name}», сэр: {error}",
        "uk": "Не вдалося повернути «{name}», сер: {error}",
        "en": "Could not restore \"{name}\", sir: {error}",
    },
}


def _say(key: str, **values: object) -> str:
    from jarvis.core.phrases import reply_language

    variants = _PHRASES[key]
    return variants.get(reply_language(), variants["en"]).format(**values)


class UndoFileChangeTool:
    """ "верни как было" -> put back the last deleted or overwritten file."""

    name: str = "undo_file_change"
    description: str = (
        "Undoes the last file deletion or overwrite done by Jarvis: puts the "
        "deleted file / folder back, or restores the previous version of an "
        "overwritten file. Use when the user says to undo, restore the "
        "deleted file, or put it back as it was. "
        "(RU: верни как было, отмени удаление, восстанови файл / "
        "UK: поверни як було, віднови файл)"
    )
    args_schema = EmptyArgs
    requires_confirmation: bool = False
    risk_level: ClassVar[RiskLevel] = RiskLevel.WRITE
    # Priority 190: before window_action's "restore (.+)" (200), which
    # took "restore the deleted file" for a window name. These patterns
    # match whole phrases only.
    voice_patterns: ClassVar[tuple[VoicePattern, ...]] = (
        # RU
        VoicePattern(
            regex=(
                r"^(?:верни\s+(?:вс[её]\s+)?как\s+было"
                r"|отмени\s+(?:удаление|перезапись)(?:\s+файла)?"
                r"|(?:верни|восстанови)\s+(?:удал[её]нн(?:ый|ую|ое)\s+)?(?:файл|папку)(?:\s+обратно)?)$"
            ),
            priority=190,
            args=lambda m: {},
        ),
        # UK
        VoicePattern(
            regex=(
                r"^(?:поверни\s+(?:вс[еє]\s+)?як\s+було"
                r"|скасуй\s+видалення(?:\s+файлу)?"
                r"|(?:поверни|віднови)\s+(?:видален(?:ий|у|е)\s+)?(?:файл|папку)(?:\s+назад)?)$"
            ),
            priority=190,
            args=lambda m: {},
        ),
        # EN
        VoicePattern(
            regex=(
                r"^(?:undo\s+(?:that|the\s+(?:delete|deletion|overwrite))"
                r"|restore\s+the\s+deleted\s+(?:file|folder)"
                r"|put\s+it\s+back(?:\s+as\s+it\s+was)?)$"
            ),
            priority=190,
            args=lambda m: {},
        ),
    )

    def __init__(self, *, store: UndoStore | None = None) -> None:
        self._store = store or UndoStore()

    async def execute(self, args: EmptyArgs) -> ToolResult:
        try:
            entry, error = await asyncio.to_thread(self._store.undo_last)
        except OSError as exc:
            entries = self._store.entries()
            name = entries[-1].name if entries else "?"
            return ToolResult(success=False, error=_say("failed", name=name, error=exc))
        if entry is None:
            return ToolResult(success=True, output=_say("nothing"))
        if error is not None:
            return ToolResult(success=False, error=_say(error, name=entry.name))
        return ToolResult(success=True, output=_say(entry.action, name=entry.name))
