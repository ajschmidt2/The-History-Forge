from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Iterable

DB_PATH = Path("data/history_forge.db")


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS projects (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS assets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id TEXT NOT NULL,
                asset_type TEXT NOT NULL,
                filename TEXT NOT NULL,
                path TEXT NOT NULL,
                size_bytes INTEGER NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(project_id, asset_type, path)
            )
            """
        )


def upsert_project(project_id: str, title: str) -> None:
    init_db()
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            INSERT INTO projects (id, title)
            VALUES (?, ?)
            ON CONFLICT(id) DO UPDATE SET
                title=excluded.title,
                updated_at=CURRENT_TIMESTAMP
            """,
            (project_id, title),
        )


def record_asset(project_id: str, asset_type: str, path: Path) -> None:
    init_db()
    resolved = path.resolve()
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO assets (project_id, asset_type, filename, path, size_bytes)
            VALUES (?, ?, ?, ?, ?)
            """,
            (project_id, asset_type, path.name, str(resolved), resolved.stat().st_size),
        )


def record_assets(project_id: str, asset_type: str, paths: Iterable[Path]) -> None:
    init_db()
    with sqlite3.connect(DB_PATH) as conn:
        for path in paths:
            resolved = path.resolve()
            conn.execute(
                """
                INSERT OR IGNORE INTO assets (project_id, asset_type, filename, path, size_bytes)
                VALUES (?, ?, ?, ?, ?)
                """,
                (project_id, asset_type, path.name, str(resolved), resolved.stat().st_size),
            )


def delete_project_records(project_id: str) -> None:
    init_db()
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("DELETE FROM assets WHERE project_id = ?", (project_id,))
        conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
