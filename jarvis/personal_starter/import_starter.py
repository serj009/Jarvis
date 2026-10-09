"""
Імпорт стартової пам'яті при першому запуску.

Запускати один раз:
    python personal_starter/import_starter.py

Читає my_preferences.yaml → записує в personal.db
Безпечний повторний запуск — перевіряє чи вже імпортовано.
"""
import sys
import os
import json
import sqlite3
from pathlib import Path


def import_starter(yaml_path="personal_starter/my_preferences.yaml",
                   stores_dir="./memory/stores"):
    """Імпортувати стартові дані."""

    print("\n🧠 J.A.R.V.I.S. — Імпорт стартової пам'яті")
    print("=" * 50)

    # ── Завантажити YAML ──
    try:
        import yaml
        with open(yaml_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        print(f"📄 Файл: {yaml_path}")
    except ImportError:
        print("❌ PyYAML не встановлений. Запустіть: pip install pyyaml")
        return False
    except FileNotFoundError:
        print(f"❌ Файл не знайдений: {yaml_path}")
        return False

    if not data:
        print("❌ Файл пустий")
        return False

    # ── Перевірити чи вже імпортовано ──
    Path(stores_dir).mkdir(parents=True, exist_ok=True)
    db_path = os.path.join(stores_dir, "personal.db")
    marker_key = "_starter_imported"

    if os.path.exists(db_path):
        conn = sqlite3.connect(db_path)
        try:
            row = conn.execute(
                "SELECT value FROM preferences WHERE key = ?", (marker_key,)
            ).fetchone()
            if row:
                print(f"⚠️  Стартова пам'ять вже імпортована ({row[0]})")
                print("   Для повторного імпорту видаліть запис '_starter_imported' з preferences")
                conn.close()
                return True
        except sqlite3.OperationalError:
            pass  # Таблиця ще не існує
        conn.close()

    # ── Імпорт через ShardManager або напряму ──
    stats = {"preferences": 0, "facts": 0, "projects": 0, "schedule": 0}

    try:
        # Спроба через ShardManager (якщо доступний)
        from memory.shard_manager import ShardManager
        sm = ShardManager(stores_dir)
        print("✅ ShardManager знайдений\n")

        # Preferences
        print("⚙️  Preferences:")
        for key, value in data.get("preferences", {}).items():
            sm.set_preference(key, str(value))
            print(f"   {key} = {value}")
            stats["preferences"] += 1

        # Facts
        print(f"\n📝 Facts:")
        for fact in data.get("facts", []):
            shard, fid = sm.remember(
                content=fact["content"],
                category=fact.get("category", "personal"),
                tags=fact.get("tags", []),
                is_permanent=fact.get("is_permanent", False),
            )
            print(f"   [{shard}] {fact['content'][:70]}")
            stats["facts"] += 1

        # Projects
        print(f"\n📋 Projects:")
        for proj in data.get("projects", []):
            desc = proj.get("description", "")
            sm.remember(
                content=f"Проект: {proj['name']} — {desc}",
                category="personal/projects",
                tags=["project", proj["name"].lower().replace(" ", "_")],
                is_permanent=True,
            )
            status = proj.get("status", "active")
            print(f"   {proj['name']} ({status})")
            stats["projects"] += 1

        # Work Schedule
        schedule = data.get("work_schedule")
        if schedule:
            print(f"\n📅 Work Schedule:")
            schedule_text = (
                f"Робочий графік: {schedule.get('type', 'shift')}, "
                f"ротація {schedule.get('rotation', 'monthly')}. "
            )
            shifts = schedule.get("shifts", {})
            for shift_name, shift_data in shifts.items():
                days = ", ".join(shift_data.get("days", []))
                hours = shift_data.get("hours", "")
                commute = shift_data.get("commute", "")
                schedule_text += f"{shift_name}: {days}, {hours}"
                if commute:
                    schedule_text += f", дорога {commute}"
                schedule_text += ". "

            note = schedule.get("note", "")
            if note:
                schedule_text += note

            sm.remember(
                content=schedule_text,
                category="personal/work",
                tags=["schedule", "work"],
                is_permanent=False,  # Может меняться
            )
            print(f"   {schedule_text[:80]}...")
            stats["schedule"] += 1

        # Маркер что импорт выполнен
        from datetime import datetime
        sm.set_preference(marker_key, datetime.now().isoformat())
        sm.close()

    except ImportError:
        # Fallback: напряму через SQLite
        print("⚠️  ShardManager не знайдений. Використовую прямий SQLite.\n")
        _import_direct_sqlite(data, db_path, stats)

    # ── Підсумок ──
    total = sum(stats.values())
    print(f"\n{'=' * 50}")
    print(f"✅ Імпорт завершено!")
    print(f"   Preferences: {stats['preferences']}")
    print(f"   Facts:       {stats['facts']}")
    print(f"   Projects:    {stats['projects']}")
    print(f"   Schedule:    {stats['schedule']}")
    print(f"   Всього:      {total} записів")
    print(f"{'=' * 50}\n")
    return True


def _import_direct_sqlite(data, db_path, stats):
    """Fallback імпорт напряму в SQLite (без ShardManager)."""
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS preferences (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            key TEXT NOT NULL UNIQUE,
            value TEXT NOT NULL,
            context TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS facts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            content TEXT NOT NULL,
            source TEXT DEFAULT 'user',
            category TEXT DEFAULT 'general',
            language TEXT DEFAULT 'auto',
            confidence REAL DEFAULT 1.0,
            tags TEXT DEFAULT '[]',
            is_permanent INTEGER DEFAULT 0,
            hit_count INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.commit()

    # Preferences
    print("⚙️  Preferences:")
    for key, value in data.get("preferences", {}).items():
        conn.execute(
            "INSERT OR REPLACE INTO preferences (key, value) VALUES (?, ?)",
            (key, str(value))
        )
        print(f"   {key} = {value}")
        stats["preferences"] += 1

    # Facts
    print(f"\n📝 Facts:")
    for fact in data.get("facts", []):
        conn.execute(
            """INSERT INTO facts (content, category, tags, is_permanent)
               VALUES (?, ?, ?, ?)""",
            (fact["content"], fact.get("category", "personal"),
             json.dumps(fact.get("tags", []), ensure_ascii=False),
             int(fact.get("is_permanent", False)))
        )
        print(f"   {fact['content'][:70]}")
        stats["facts"] += 1

    # Projects
    print(f"\n📋 Projects:")
    for proj in data.get("projects", []):
        desc = proj.get("description", "")
        conn.execute(
            """INSERT INTO facts (content, category, tags, is_permanent)
               VALUES (?, ?, ?, ?)""",
            (f"Проект: {proj['name']} — {desc}", "personal/projects",
             json.dumps(["project", proj["name"].lower().replace(" ", "_")]), 1)
        )
        print(f"   {proj['name']}")
        stats["projects"] += 1

    # Маркер
    from datetime import datetime
    conn.execute(
        "INSERT OR REPLACE INTO preferences (key, value) VALUES (?, ?)",
        ("_starter_imported", datetime.now().isoformat())
    )
    conn.commit()
    conn.close()


if __name__ == "__main__":
    success = import_starter()
    sys.exit(0 if success else 1)
