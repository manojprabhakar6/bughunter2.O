import os
import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


def _database_path() -> Path:
    configured_path = os.environ.get("BUG_HUNTER_DATABASE")
    if configured_path:
        return Path(configured_path)
    return Path(__file__).resolve().parents[1] / "data" / "bug_hunter.sqlite3"


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    database_path = _database_path()
    database_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _migration_1(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS submissions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
            filename TEXT NOT NULL,
            language TEXT NOT NULL,
            original_code TEXT NOT NULL,
            corrected_code TEXT NOT NULL,
            findings_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL COLLATE NOCASE UNIQUE,
            email TEXT COLLATE NOCASE UNIQUE,
            password_hash TEXT NOT NULL,
            password_salt TEXT NOT NULL,
            created_at TEXT NOT NULL,
            is_demo INTEGER NOT NULL DEFAULT 0,
            demo_expires_at TEXT
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS user_sessions (
            token_hash TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL
        )
        """
    )


def _migration_2(connection: sqlite3.Connection) -> None:
    table = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'submissions'"
    ).fetchone()
    if table is None:
        raise RuntimeError("The submissions table was not created by migration 1.")

    table_sql = table["sql"] or ""
    columns = {
        row["name"] for row in connection.execute("PRAGMA table_info(submissions)")
    }
    python_only = re.search(
        r"check\s*\(\s*language\s*=\s*['\"]python['\"]\s*\)",
        table_sql,
        re.IGNORECASE,
    )
    if not python_only and "owner_id" in columns:
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS submissions_owner_created
            ON submissions(owner_id, created_at DESC, id DESC)
            """
        )
        return

    connection.execute(
        """
        CREATE TABLE submissions_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
            filename TEXT NOT NULL,
            language TEXT NOT NULL,
            original_code TEXT NOT NULL,
            corrected_code TEXT NOT NULL,
            findings_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    owner_column = "owner_id" if "owner_id" in columns else "NULL"
    connection.execute(
        f"""
        INSERT INTO submissions_new (
            id, owner_id, filename, language, original_code, corrected_code,
            findings_json, created_at, updated_at
        )
        SELECT id, {owner_column}, filename, language, original_code,
               corrected_code, findings_json, created_at, updated_at
        FROM submissions
        """
    )
    connection.execute("DROP TABLE submissions")
    connection.execute("ALTER TABLE submissions_new RENAME TO submissions")
    connection.execute(
        """
        CREATE INDEX submissions_owner_created
        ON submissions(owner_id, created_at DESC, id DESC)
        """
    )


def _migration_3(connection: sqlite3.Connection) -> None:
    columns = {row["name"] for row in connection.execute("PRAGMA table_info(users)")}
    if "email" not in columns:
        connection.execute("ALTER TABLE users ADD COLUMN email TEXT")
        connection.execute("UPDATE users SET email = username WHERE email IS NULL")
    connection.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS users_email_unique
        ON users(email COLLATE NOCASE)
        """
    )


def _migration_4(connection: sqlite3.Connection) -> None:
    columns = {row["name"] for row in connection.execute("PRAGMA table_info(users)")}
    if "is_demo" not in columns:
        connection.execute(
            "ALTER TABLE users ADD COLUMN is_demo INTEGER NOT NULL DEFAULT 0"
        )
    if "demo_expires_at" not in columns:
        connection.execute("ALTER TABLE users ADD COLUMN demo_expires_at TEXT")


def _migration_5(connection: sqlite3.Connection) -> None:
    submission_table = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'submissions'"
    ).fetchone()
    if submission_table is None:
        _migration_1(connection)
    columns = {
        row["name"] for row in connection.execute("PRAGMA table_info(submissions)")
    }
    if "program_goal" not in columns:
        connection.execute(
            "ALTER TABLE submissions ADD COLUMN program_goal TEXT NOT NULL DEFAULT ''"
        )


def _migration_6(connection: sqlite3.Connection) -> None:
    columns = {
        row["name"] for row in connection.execute("PRAGMA table_info(submissions)")
    }
    if "simulation_input" not in columns:
        connection.execute(
            "ALTER TABLE submissions ADD COLUMN simulation_input TEXT NOT NULL DEFAULT ''"
        )
    if "simulation_json" not in columns:
        connection.execute("ALTER TABLE submissions ADD COLUMN simulation_json TEXT")


def _migration_7(connection: sqlite3.Connection) -> None:
    columns = {
        row["name"] for row in connection.execute("PRAGMA table_info(submissions)")
    }
    if "expected_output" not in columns:
        connection.execute("ALTER TABLE submissions ADD COLUMN expected_output TEXT")


_MIGRATIONS = (
    (1, _migration_1),
    (2, _migration_2),
    (3, _migration_3),
    (4, _migration_4),
    (5, _migration_5),
    (6, _migration_6),
    (7, _migration_7),
)


def initialize_database() -> None:
    with connect() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        for version, migration in _MIGRATIONS:
            applied = connection.execute(
                "SELECT 1 FROM schema_migrations WHERE version = ?", (version,)
            ).fetchone()
            if applied is not None:
                continue
            connection.execute("BEGIN")
            migration(connection)
            connection.execute(
                "INSERT INTO schema_migrations (version) VALUES (?)", (version,)
            )
            connection.commit()
