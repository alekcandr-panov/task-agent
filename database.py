import sqlite3
import os
from datetime import datetime
from typing import Optional

DB_PATH = os.getenv("DB_PATH", "tasks.db")


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id     INTEGER NOT NULL,
                title       TEXT NOT NULL,
                description TEXT,
                assignee    TEXT,
                deadline    TEXT,
                priority    TEXT DEFAULT 'medium',
                status      TEXT DEFAULT 'open',
                source_msg  TEXT,
                created_at  TEXT DEFAULT (datetime('now')),
                done_at     TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS reminded (
                task_id     INTEGER,
                reminded_at TEXT,
                type        TEXT,
                PRIMARY KEY (task_id, type)
            )
        """)
        conn.commit()


def add_task(chat_id: int, title: str, description: str = None,
             assignee: str = None, deadline: str = None,
             priority: str = "medium", source_msg: str = None) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO tasks
               (chat_id, title, description, assignee, deadline, priority, source_msg)
               VALUES (?,?,?,?,?,?,?)""",
            (chat_id, title, description, assignee, deadline, priority, source_msg)
        )
        conn.commit()
        return cur.lastrowid


def get_tasks(chat_id: int, status: str = "open") -> list:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM tasks WHERE chat_id=? AND status=? ORDER BY deadline ASC NULLS LAST, priority DESC",
            (chat_id, status)
        ).fetchall()
        return [dict(r) for r in rows]


def get_task(task_id: int) -> Optional[dict]:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        return dict(row) if row else None


def close_task(task_id: int):
    with get_conn() as conn:
        conn.execute(
            "UPDATE tasks SET status='done', done_at=datetime('now') WHERE id=?",
            (task_id,)
        )
        conn.commit()


def get_due_soon(hours: int = 24) -> list:
    """Tasks due within `hours` hours, not yet reminded."""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT t.* FROM tasks t
            LEFT JOIN reminded r ON r.task_id = t.id AND r.type='due_soon'
            WHERE t.status='open'
              AND t.deadline IS NOT NULL
              AND datetime(t.deadline) <= datetime('now', ? || ' hours')
              AND datetime(t.deadline) > datetime('now')
              AND r.task_id IS NULL
        """, (str(hours),)).fetchall()
        return [dict(r) for r in rows]


def get_overdue() -> list:
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT t.* FROM tasks t
            LEFT JOIN reminded r ON r.task_id = t.id AND r.type='overdue'
            WHERE t.status='open'
              AND t.deadline IS NOT NULL
              AND datetime(t.deadline) < datetime('now')
              AND r.task_id IS NULL
        """).fetchall()
        return [dict(r) for r in rows]


def mark_reminded(task_id: int, reminder_type: str):
    with get_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO reminded (task_id, reminded_at, type) VALUES (?,datetime('now'),?)",
            (task_id, reminder_type)
        )
        conn.commit()


def get_summary(chat_id: int) -> dict:
    with get_conn() as conn:
        total = conn.execute(
            "SELECT COUNT(*) FROM tasks WHERE chat_id=? AND status='open'", (chat_id,)
        ).fetchone()[0]
        overdue = conn.execute(
            """SELECT COUNT(*) FROM tasks
               WHERE chat_id=? AND status='open'
               AND deadline IS NOT NULL AND datetime(deadline) < datetime('now')""",
            (chat_id,)
        ).fetchone()[0]
        due_today = conn.execute(
            """SELECT COUNT(*) FROM tasks
               WHERE chat_id=? AND status='open'
               AND deadline IS NOT NULL
               AND date(deadline) = date('now')""",
            (chat_id,)
        ).fetchone()[0]
        done_week = conn.execute(
            """SELECT COUNT(*) FROM tasks
               WHERE chat_id=? AND status='done'
               AND done_at >= datetime('now', '-7 days')""",
            (chat_id,)
        ).fetchone()[0]
        return {
            "total_open": total,
            "overdue": overdue,
            "due_today": due_today,
            "done_this_week": done_week
        }
