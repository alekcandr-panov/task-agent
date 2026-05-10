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
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id      INTEGER NOT NULL,
                source_chat_id INTEGER,
                title        TEXT NOT NULL,
                description  TEXT,
                assignee     TEXT,
                deadline     TEXT,
                priority     TEXT DEFAULT 'medium',
                status       TEXT DEFAULT 'open',
                source_msg   TEXT,
                created_at   TEXT DEFAULT (datetime('now')),
                done_at      TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS meetings (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_id       INTEGER NOT NULL,
                source_chat_id INTEGER,
                source_msg_id  INTEGER,
                proposed_by_id INTEGER,
                title          TEXT NOT NULL,
                date           TEXT,
                time           TEXT,
                duration_min   INTEGER DEFAULT 60,
                location       TEXT,
                participants   TEXT,
                description    TEXT,
                proposed_by    TEXT,
                source_msg     TEXT,
                status         TEXT DEFAULT 'pending',
                calendar_uid   TEXT,
                created_at     TEXT DEFAULT (datetime('now'))
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


# ── Tasks ──────────────────────────────────────────────────────────────────────

def add_task(chat_id, title, description=None, assignee=None,
             deadline=None, priority="medium", source_msg=None,
             source_chat_id=None) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO tasks
               (chat_id, source_chat_id, title, description, assignee,
                deadline, priority, source_msg)
               VALUES (?,?,?,?,?,?,?,?)""",
            (chat_id, source_chat_id, title, description, assignee,
             deadline, priority, source_msg)
        )
        conn.commit()
        return cur.lastrowid


def get_tasks(chat_id, status="open") -> list:
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT * FROM tasks WHERE chat_id=? AND status=?
               ORDER BY
                 CASE priority WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END,
                 deadline ASC NULLS LAST""",
            (chat_id, status)
        ).fetchall()
        return [dict(r) for r in rows]


def get_open_tasks_for_chat(source_chat_id: int) -> list:
    """Get open tasks that originated from a specific chat."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM tasks WHERE source_chat_id=? AND status='open'",
            (source_chat_id,)
        ).fetchall()
        return [dict(r) for r in rows]


def get_task(task_id) -> Optional[dict]:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        return dict(row) if row else None


def close_task(task_id):
    with get_conn() as conn:
        conn.execute(
            "UPDATE tasks SET status='done', done_at=datetime('now') WHERE id=?",
            (task_id,)
        )
        conn.commit()


def get_due_soon(hours=24) -> list:
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT t.* FROM tasks t
            LEFT JOIN reminded r ON r.task_id=t.id AND r.type='due_soon'
            WHERE t.status='open' AND t.deadline IS NOT NULL
              AND datetime(t.deadline) <= datetime('now', ? || ' hours')
              AND datetime(t.deadline) > datetime('now')
              AND r.task_id IS NULL
        """, (str(hours),)).fetchall()
        return [dict(r) for r in rows]


def get_overdue() -> list:
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT t.* FROM tasks t
            LEFT JOIN reminded r ON r.task_id=t.id AND r.type='overdue'
            WHERE t.status='open' AND t.deadline IS NOT NULL
              AND datetime(t.deadline) < datetime('now')
              AND r.task_id IS NULL
        """).fetchall()
        return [dict(r) for r in rows]


def mark_reminded(task_id, reminder_type):
    with get_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO reminded (task_id, reminded_at, type) VALUES (?,datetime('now'),?)",
            (task_id, reminder_type)
        )
        conn.commit()


def get_summary(chat_id) -> dict:
    with get_conn() as conn:
        total = conn.execute(
            "SELECT COUNT(*) FROM tasks WHERE chat_id=? AND status='open'", (chat_id,)
        ).fetchone()[0]
        overdue = conn.execute(
            """SELECT COUNT(*) FROM tasks WHERE chat_id=? AND status='open'
               AND deadline IS NOT NULL AND datetime(deadline)<datetime('now')""",
            (chat_id,)
        ).fetchone()[0]
        due_today = conn.execute(
            """SELECT COUNT(*) FROM tasks WHERE chat_id=? AND status='open'
               AND deadline IS NOT NULL AND date(deadline)=date('now')""",
            (chat_id,)
        ).fetchone()[0]
        done_week = conn.execute(
            """SELECT COUNT(*) FROM tasks WHERE chat_id=? AND status='done'
               AND done_at>=datetime('now','-7 days')""",
            (chat_id,)
        ).fetchone()[0]
        return {
            "total_open": total, "overdue": overdue,
            "due_today": due_today, "done_this_week": done_week
        }


# ── Meetings ───────────────────────────────────────────────────────────────────

def save_meeting(owner_id, title, date=None, time=None, duration_min=60,
                 location=None, participants=None, description=None,
                 proposed_by=None, source_msg=None,
                 source_chat_id=None, source_msg_id=None,
                 proposed_by_id=None) -> int:
    participants_str = ", ".join(participants) if participants else None
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO meetings
               (owner_id, source_chat_id, source_msg_id, proposed_by_id,
                title, date, time, duration_min, location,
                participants, description, proposed_by, source_msg)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (owner_id, source_chat_id, source_msg_id, proposed_by_id,
             title, date, time, duration_min, location,
             participants_str, description, proposed_by, source_msg)
        )
        conn.commit()
        return cur.lastrowid


def update_meeting(meeting_id, **kwargs):
    if not kwargs:
        return
    sets = ", ".join(f"{k}=?" for k in kwargs)
    vals = list(kwargs.values()) + [meeting_id]
    with get_conn() as conn:
        conn.execute(f"UPDATE meetings SET {sets} WHERE id=?", vals)
        conn.commit()


def get_meeting(meeting_id) -> Optional[dict]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM meetings WHERE id=?", (meeting_id,)
        ).fetchone()
        return dict(row) if row else None


def get_upcoming_meetings(owner_id, limit=5) -> list:
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT * FROM meetings WHERE owner_id=? AND status='confirmed'
               AND date >= date('now')
               ORDER BY date ASC, time ASC LIMIT ?""",
            (owner_id, limit)
        ).fetchall()
        return [dict(r) for r in rows]


def get_meetings_today(owner_id) -> list:
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT * FROM meetings WHERE owner_id=?
               AND date=date('now') AND status='confirmed'
               ORDER BY time ASC""",
            (owner_id,)
        ).fetchall()
        return [dict(r) for r in rows]
