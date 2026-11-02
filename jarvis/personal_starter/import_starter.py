"""
Імпорт стартової пам'яті в пам'ять Jarvis (memory.db).

Запускати один раз:
    python -m jarvis.personal_starter.import_starter
    python -m jarvis.personal_starter.import_starter --force   # імпорт ще раз

Читає %APPDATA%/Jarvis/memory/my_preferences.yaml і записує:
  - facts / projects / work_schedule -> memory.db через MemoryStore (той самий,
    що читає Jarvis; текст шифрується DPAPI);
  - preferences -> memory.db як факти-вподобання з тегом auto_setting_pref
    (auto_recall подає їх моделі як інструкції користувача);
  - profile -> user_profile.json через UserProfile (ім'я, місто, ...).

Файл my_preferences.yaml особистий і НЕ зберігається в репозиторії: Jarvis
сам створює порожній шаблон при першому налаштуванні пам'яті
(jarvis.memory.preferences_file).

Повторний запуск безпечний: після імпорту поруч з memory.db з'являється
маркер .starter_imported, і без --force імпорт вдруге не виконується.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import UTC, datetime
from pathlib import Path

from jarvis.memory.preferences_file import default_preferences_path, ensure_preferences_file
from jarvis.memory.store import MemoryStore
from jarvis.memory.user_profile import UserProfile

MARKER_FILENAME = ".starter_imported"
# auto_recall injects facts with this tag under "[User preferences:]".
PREFERENCE_TAG = "auto_setting_pref"
# Marks everything this script wrote, so it can be told apart later.
IMPORT_TAG = "starter_import"

_SECTIONS = ("preferences", "facts", "projects", "work_schedule", "profile")


def _load_yaml(path: Path) -> dict:
    import yaml

    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data if isinstance(data, dict) else {}


def _has_content(data: dict) -> bool:
    return any(data.get(section) for section in _SECTIONS)


def _schedule_text(schedule: dict) -> str:
    text = (
        f"Робочий графік: {schedule.get('type', 'shift')}, "
        f"ротація {schedule.get('rotation', 'monthly')}. "
    )
    for name, shift in (schedule.get("shifts") or {}).items():
        shift = shift or {}
        days = ", ".join(shift.get("days", []))
        text += f"{name}: {days}, {shift.get('hours', '')}"
        if shift.get("commute"):
            text += f", дорога {shift['commute']}"
        text += ". "
    if schedule.get("note"):
        text += str(schedule["note"])
    return text.strip()


async def _import_into_memory(data: dict, memory_dir: Path) -> dict[str, int]:
    stats = {"preferences": 0, "facts": 0, "projects": 0, "schedule": 0, "profile": 0}

    store = MemoryStore(db_path=memory_dir / "memory.db")
    await store.open()
    try:
        # language="" -> MemoryStore's AutoTagger detects ru/uk/en itself.
        for key, value in (data.get("preferences") or {}).items():
            await store.add(
                f"Налаштування користувача: {key} = {value}",
                category="preference", source="import", language="",
                tags=[PREFERENCE_TAG, IMPORT_TAG], is_permanent=True,
            )
            print(f"   ⚙️  {key} = {value}")
            stats["preferences"] += 1

        for fact in data.get("facts") or []:
            if isinstance(fact, str):
                fact = {"content": fact}
            content = str(fact.get("content") or "").strip()
            if not content:
                continue
            await store.add(
                content,
                category=fact.get("category", "personal"), source="import", language="",
                tags=list(fact.get("tags") or []) + [IMPORT_TAG],
                is_permanent=bool(fact.get("is_permanent", False)),
            )
            print(f"   📝 {content[:70]}")
            stats["facts"] += 1

        for proj in data.get("projects") or []:
            name = str((proj or {}).get("name") or "").strip()
            if not name:
                continue
            await store.add(
                f"Проект: {name} — {proj.get('description', '')}".rstrip(" —"),
                category="personal/projects", source="import", language="",
                tags=["project", name.lower().replace(" ", "_"), IMPORT_TAG],
                is_permanent=True,
            )
            print(f"   📋 {name}")
            stats["projects"] += 1

        schedule = data.get("work_schedule")
        if isinstance(schedule, dict) and schedule:
            text = _schedule_text(schedule)
            await store.add(
                text,
                category="personal/work", source="import", language="",
                tags=["schedule", "work", IMPORT_TAG],
                is_permanent=False,  # графік може змінюватися
            )
            print(f"   📅 {text[:70]}")
            stats["schedule"] += 1
    finally:
        await store.close()

    profile_data = data.get("profile") or {}
    if isinstance(profile_data, dict) and profile_data:
        profile = UserProfile(path=memory_dir / "user_profile.json")
        for key, value in profile_data.items():
            if value in (None, "", [], {}):
                continue
            profile.set(key, value)
            print(f"   👤 {key} = {value}")
            stats["profile"] += 1

    return stats


def import_starter(
    yaml_path: str | Path | None = None,
    memory_dir: str | Path | None = None,
    *,
    force: bool = False,
) -> bool:
    """Import my_preferences.yaml into Jarvis memory. Returns True on success.

    *memory_dir* is the folder that holds memory.db and user_profile.json
    (default: <data dir>/memory, the same folder Jarvis uses).
    """
    path = Path(yaml_path) if yaml_path else default_preferences_path()
    target_dir = Path(memory_dir) if memory_dir else default_preferences_path().parent

    print("\n🧠 J.A.R.V.I.S. — імпорт стартової пам'яті")
    print("=" * 50)

    if not path.exists():
        ensure_preferences_file(path)
        print(f"📝 Створено порожній шаблон: {path}")
        print("   Заповніть його і запустіть імпорт ще раз.")
        return False

    try:
        data = _load_yaml(path)
    except ImportError:
        print("❌ PyYAML не встановлений. Запустіть: pip install pyyaml")
        return False
    except Exception as exc:  # битий YAML: показати причину, не traceback
        print(f"❌ Не вдалося прочитати {path}: {exc}")
        return False

    if not _has_content(data):
        print(f"❌ Файл порожній: {path}")
        print("   Заповніть його і запустіть імпорт ще раз.")
        return False

    marker = target_dir / MARKER_FILENAME
    if marker.exists() and not force:
        imported_at = marker.read_text(encoding="utf-8").strip()
        print(f"⚠️  Стартова пам'ять вже імпортована ({imported_at}).")
        print("   Щоб імпортувати ще раз: --force")
        return True

    print(f"📄 Файл: {path}")
    print(f"💾 Пам'ять: {target_dir / 'memory.db'}\n")
    stats = asyncio.run(_import_into_memory(data, target_dir))

    target_dir.mkdir(parents=True, exist_ok=True)
    marker.write_text(datetime.now(UTC).isoformat(), encoding="utf-8")

    print(f"\n{'=' * 50}")
    print("✅ Імпорт завершено!")
    for name, count in stats.items():
        print(f"   {name:<12} {count}")
    print(f"   {'всього':<12} {sum(stats.values())} записів")
    print(f"{'=' * 50}\n")
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Import my_preferences.yaml into Jarvis memory")
    parser.add_argument("--file", help="path to my_preferences.yaml (default: data dir)")
    parser.add_argument("--force", action="store_true", help="import again even if done before")
    args = parser.parse_args(argv)
    return 0 if import_starter(args.file, force=args.force) else 1


if __name__ == "__main__":
    sys.exit(main())
