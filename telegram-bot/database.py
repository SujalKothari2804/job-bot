"""
ReferJobs Member Database
Tracks when each member joined and handles subscription expiry
"""

import sqlite3
import os
from datetime import datetime, timedelta

DB_PATH = os.path.join(os.path.dirname(__file__), "members.db")


def get_conn():
    return sqlite3.connect(DB_PATH)


def init_db():
    """Create tables if they don't exist."""
    with get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS members (
                user_id       INTEGER PRIMARY KEY,
                username      TEXT,
                full_name     TEXT,
                joined_at     TEXT NOT NULL,
                expires_at    TEXT NOT NULL,
                status        TEXT DEFAULT 'active',
                warned        INTEGER DEFAULT 0
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS removed_members (
                user_id       INTEGER,
                username      TEXT,
                full_name     TEXT,
                joined_at     TEXT,
                removed_at    TEXT,
                reason        TEXT DEFAULT 'expired'
            )
        """)
        conn.commit()
    print("[DB] Database initialized")


def add_member(user_id: int, username: str, full_name: str):
    """Add a new member with 3 month expiry."""
    now        = datetime.now()
    expires_at = now + timedelta(days=90)  # 3 months

    with get_conn() as conn:
        conn.execute("""
            INSERT OR REPLACE INTO members
            (user_id, username, full_name, joined_at, expires_at, status, warned)
            VALUES (?, ?, ?, ?, ?, 'active', 0)
        """, (
            user_id,
            username or "",
            full_name or "",
            now.isoformat(),
            expires_at.isoformat(),
        ))
        conn.commit()
    print(f"[DB] Added member {full_name} (@{username}) — expires {expires_at.strftime('%d %b %Y')}")


def get_expiring_soon(days_before: int = 7) -> list[dict]:
    """Get members expiring within N days (for warning message)."""
    now        = datetime.now()
    warn_cutoff = now + timedelta(days=days_before)

    with get_conn() as conn:
        rows = conn.execute("""
            SELECT user_id, username, full_name, joined_at, expires_at
            FROM members
            WHERE status = 'active'
              AND warned = 0
              AND expires_at <= ?
              AND expires_at > ?
        """, (warn_cutoff.isoformat(), now.isoformat())).fetchall()

    return [
        {
            "user_id":   r[0],
            "username":  r[1],
            "full_name": r[2],
            "joined_at": r[3],
            "expires_at": r[4],
        }
        for r in rows
    ]


def get_expired() -> list[dict]:
    """Get members whose subscription has expired."""
    now = datetime.now()

    with get_conn() as conn:
        rows = conn.execute("""
            SELECT user_id, username, full_name, joined_at, expires_at
            FROM members
            WHERE status = 'active'
              AND expires_at <= ?
        """, (now.isoformat(),)).fetchall()

    return [
        {
            "user_id":   r[0],
            "username":  r[1],
            "full_name": r[2],
            "joined_at": r[3],
            "expires_at": r[4],
        }
        for r in rows
    ]


def mark_warned(user_id: int):
    """Mark member as warned (so we don't warn them again)."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE members SET warned = 1 WHERE user_id = ?",
            (user_id,)
        )
        conn.commit()


def mark_removed(user_id: int):
    """Move member from active to removed table."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT user_id, username, full_name, joined_at FROM members WHERE user_id = ?",
            (user_id,)
        ).fetchone()

        if row:
            conn.execute("""
                INSERT INTO removed_members
                (user_id, username, full_name, joined_at, removed_at, reason)
                VALUES (?, ?, ?, ?, ?, 'expired')
            """, (row[0], row[1], row[2], row[3], datetime.now().isoformat()))

            conn.execute(
                "UPDATE members SET status = 'removed' WHERE user_id = ?",
                (user_id,)
            )
            conn.commit()


def renew_member(user_id: int):
    """Renew a member's subscription for another 3 months from today."""
    new_expiry = datetime.now() + timedelta(days=90)
    with get_conn() as conn:
        conn.execute("""
            UPDATE members
            SET expires_at = ?, status = 'active', warned = 0
            WHERE user_id = ?
        """, (new_expiry.isoformat(), user_id))
        conn.commit()
    print(f"[DB] Renewed member {user_id} — new expiry {new_expiry.strftime('%d %b %Y')}")


def get_all_active() -> list[dict]:
    """Get all active members (for admin view)."""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT user_id, username, full_name, joined_at, expires_at, warned
            FROM members
            WHERE status = 'active'
            ORDER BY expires_at ASC
        """).fetchall()

    return [
        {
            "user_id":   r[0],
            "username":  r[1],
            "full_name": r[2],
            "joined_at": r[3],
            "expires_at": r[4],
            "warned":    r[5],
        }
        for r in rows
    ]


def get_member(user_id: int) -> dict | None:
    """Get a single member's details."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT user_id, username, full_name, joined_at, expires_at, status, warned FROM members WHERE user_id = ?",
            (user_id,)
        ).fetchone()

    if not row:
        return None
    return {
        "user_id":   row[0],
        "username":  row[1],
        "full_name": row[2],
        "joined_at": row[3],
        "expires_at": row[4],
        "status":    row[5],
        "warned":    row[6],
    }
