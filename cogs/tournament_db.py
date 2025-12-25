# tournament_db.py
# ✅ SQLite stability + speed (WAL + busy_timeout + foreign keys)
# ✅ Write retry wrapper (run_db) to gracefully handle "database is locked"
# ✅ Centralized DB helpers so cogs never open ad-hoc sqlite connections
#
# Cleanup Roadmap v1 - Step 5:
# - Shared connection + retry helpers moved to core.db (no behavior changes)
# - This file keeps the same public API used by the cogs.

from __future__ import annotations

import time
from typing import Any, Dict, Optional

import sqlite3

from core.config import DB_PATH  # re-export for legacy imports
from core import db as core_db

from core.db import get_db_connection, run_db, with_conn


def _now() -> int:
    return int(time.time())


def _dict(row: Optional[sqlite3.Row]) -> Optional[Dict[str, Any]]:
    return dict(row) if row is not None else None
# -----------------------------
# Schema
# -----------------------------
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS tournaments (
    guild_id            INTEGER NOT NULL,
    tournament_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    name                TEXT NOT NULL,
    status              TEXT NOT NULL DEFAULT 'active',   -- active/archived/deleted
    created_at          INTEGER NOT NULL,
    updated_at          INTEGER NOT NULL,

    team_size           INTEGER NOT NULL DEFAULT 2,       -- 1..6
    best_of             INTEGER NOT NULL DEFAULT 3,
    join_open           INTEGER NOT NULL DEFAULT 1,       -- 0/1
    open_join_mode      INTEGER NOT NULL DEFAULT 1,       -- 0=invite only, 1=open
    captain_scoring     INTEGER NOT NULL DEFAULT 0,       -- 0/1
    screenshots_required INTEGER NOT NULL DEFAULT 0,      -- 0/1

    category_id         INTEGER,
    admin_channel_id    INTEGER,
    announcements_channel_id INTEGER,
    rules_channel_id    INTEGER,
    create_team_channel_id INTEGER,
    teams_channel_id    INTEGER,
    chat_channel_id     INTEGER,
    bracket_channel_id  INTEGER,
    results_channel_id  INTEGER
);

CREATE INDEX IF NOT EXISTS idx_tournaments_guild_status
ON tournaments(guild_id, status);

CREATE INDEX IF NOT EXISTS idx_tournaments_guild_updated
ON tournaments(guild_id, updated_at);

CREATE TABLE IF NOT EXISTS participants (
    guild_id        INTEGER NOT NULL,
    user_id         INTEGER NOT NULL,
    is_bot          INTEGER NOT NULL DEFAULT 0,
    display_name    TEXT,
    created_at      INTEGER NOT NULL,
    updated_at      INTEGER NOT NULL,
    PRIMARY KEY (guild_id, user_id)
);

CREATE INDEX IF NOT EXISTS idx_participants_guild_bot
ON participants(guild_id, is_bot);

CREATE TABLE IF NOT EXISTS tournament_participants (
    tournament_id   INTEGER NOT NULL,
    guild_id        INTEGER NOT NULL,
    user_id         INTEGER NOT NULL,
    joined_at       INTEGER NOT NULL,
    PRIMARY KEY (tournament_id, user_id),
    FOREIGN KEY (tournament_id) REFERENCES tournaments(tournament_id) ON DELETE CASCADE,
    FOREIGN KEY (guild_id, user_id) REFERENCES participants(guild_id, user_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_tp_tournament
ON tournament_participants(tournament_id);

CREATE TABLE IF NOT EXISTS teams (
    tournament_id   INTEGER NOT NULL,
    team_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id        INTEGER NOT NULL,
    name            TEXT NOT NULL,
    captain_user_id INTEGER NOT NULL,
    role_id         INTEGER,
    hub_channel_id  INTEGER,
    ready           INTEGER NOT NULL DEFAULT 0,
    is_bot_team     INTEGER NOT NULL DEFAULT 0,
    created_at      INTEGER NOT NULL,
    updated_at      INTEGER NOT NULL,
    FOREIGN KEY (tournament_id) REFERENCES tournaments(tournament_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_teams_tournament_ready
ON teams(tournament_id, ready);

CREATE INDEX IF NOT EXISTS idx_teams_tournament_bot
ON teams(tournament_id, is_bot_team);

CREATE TABLE IF NOT EXISTS team_members (
    tournament_id   INTEGER NOT NULL,
    team_id         INTEGER NOT NULL,
    guild_id        INTEGER NOT NULL,
    user_id         INTEGER NOT NULL,
    joined_at       INTEGER NOT NULL,
    PRIMARY KEY (tournament_id, user_id),
    FOREIGN KEY (tournament_id) REFERENCES tournaments(tournament_id) ON DELETE CASCADE,
    FOREIGN KEY (team_id) REFERENCES teams(team_id) ON DELETE CASCADE,
    FOREIGN KEY (guild_id, user_id) REFERENCES participants(guild_id, user_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_team_members_team
ON team_members(team_id);

CREATE TABLE IF NOT EXISTS bracket_matches (
    tournament_id   INTEGER NOT NULL,
    match_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    round_no        INTEGER NOT NULL,
    match_no        INTEGER NOT NULL,
    team_a_id       INTEGER,
    team_b_id       INTEGER,
    winner_team_id  INTEGER,
    score_a         INTEGER,
    score_b         INTEGER,
    status          TEXT NOT NULL DEFAULT 'pending', -- pending/active/complete
    match_channel_id INTEGER,
    created_at      INTEGER NOT NULL,
    updated_at      INTEGER NOT NULL,
    FOREIGN KEY (tournament_id) REFERENCES tournaments(tournament_id) ON DELETE CASCADE,
    FOREIGN KEY (team_a_id) REFERENCES teams(team_id) ON DELETE SET NULL,
    FOREIGN KEY (team_b_id) REFERENCES teams(team_id) ON DELETE SET NULL,
    FOREIGN KEY (winner_team_id) REFERENCES teams(team_id) ON DELETE SET NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_bracket_unique_slot
ON bracket_matches(tournament_id, round_no, match_no);

CREATE INDEX IF NOT EXISTS idx_bracket_status
ON bracket_matches(tournament_id, status);

CREATE TABLE IF NOT EXISTS action_log (
    guild_id        INTEGER NOT NULL,
    tournament_id   INTEGER,
    action          TEXT NOT NULL,
    details_json    TEXT,
    created_at      INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_action_log_guild_time
ON action_log(guild_id, created_at);
"""


@with_conn
def init_db(conn: sqlite3.Connection) -> None:
    # schema creation is a write, so wrap in retry
    def _write():
        conn.executescript(SCHEMA_SQL)
    run_db(_write)


# -----------------------------
# Tiny SQL helpers
# -----------------------------
def _fetchone(conn: sqlite3.Connection, sql: str, params: Tuple[Any, ...] = ()) -> Optional[sqlite3.Row]:
    return conn.execute(sql, params).fetchone()


def _fetchall(conn: sqlite3.Connection, sql: str, params: Tuple[Any, ...] = ()) -> List[sqlite3.Row]:
    return conn.execute(sql, params).fetchall()


def _begin(conn: sqlite3.Connection) -> None:
    conn.execute("BEGIN IMMEDIATE;")


def _commit(conn: sqlite3.Connection) -> None:
    conn.execute("COMMIT;")


def _rollback(conn: sqlite3.Connection) -> None:
    conn.execute("ROLLBACK;")


# -----------------------------
# Tournaments
# -----------------------------
@with_conn
def create_tournament(
    conn: sqlite3.Connection,
    guild_id: int,
    name: str,
    team_size: int = 2,
    best_of: int = 3,
    join_open: bool = True,
    open_join_mode: bool = True,
) -> int:
    now = _now()

    def _write() -> int:
        _begin(conn)
        try:
            cur = conn.execute(
                """
                INSERT INTO tournaments
                (guild_id, name, status, created_at, updated_at, team_size, best_of, join_open, open_join_mode)
                VALUES (?, ?, 'active', ?, ?, ?, ?, ?, ?)
                """,
                (guild_id, name, now, now, int(team_size), int(best_of), int(join_open), int(open_join_mode)),
            )
            tid = int(cur.lastrowid)
            _commit(conn)
            return tid
        except Exception:
            _rollback(conn)
            raise

    return run_db(_write)


@with_conn
def get_active_tournament(conn: sqlite3.Connection, guild_id: int) -> Optional[Dict[str, Any]]:
    row = _fetchone(
        conn,
        """
        SELECT * FROM tournaments
        WHERE guild_id = ? AND status = 'active'
        ORDER BY updated_at DESC
        LIMIT 1
        """,
        (guild_id,),
    )
    return _dict(row)


@with_conn
def get_tournament(conn: sqlite3.Connection, tournament_id: int) -> Optional[Dict[str, Any]]:
    return _dict(_fetchone(conn, "SELECT * FROM tournaments WHERE tournament_id = ? LIMIT 1", (tournament_id,)))


@with_conn
def set_tournament_setting(conn: sqlite3.Connection, tournament_id: int, key: str, value: Any) -> None:
    allowed = {
        "team_size",
        "best_of",
        "join_open",
        "open_join_mode",
        "captain_scoring",
        "screenshots_required",
        "status",
        "name",
    }
    if key not in allowed:
        raise ValueError(f"Invalid tournament setting: {key}")

    def _write():
        conn.execute(
            f"UPDATE tournaments SET {key} = ?, updated_at = ? WHERE tournament_id = ?",
            (value, _now(), tournament_id),
        )

    run_db(_write)


@with_conn
def update_tournament_channels(conn: sqlite3.Connection, tournament_id: int, **channel_ids: Any) -> None:
    allowed = {
        "category_id",
        "admin_channel_id",
        "announcements_channel_id",
        "rules_channel_id",
        "create_team_channel_id",
        "teams_channel_id",
        "chat_channel_id",
        "bracket_channel_id",
        "results_channel_id",
    }
    sets: List[str] = []
    vals: List[Any] = []

    for k, v in channel_ids.items():
        if k in allowed:
            sets.append(f"{k} = ?")
            vals.append(v)

    if not sets:
        return

    vals.append(_now())
    vals.append(tournament_id)

    def _write():
        conn.execute(
            f"UPDATE tournaments SET {', '.join(sets)}, updated_at = ? WHERE tournament_id = ?",
            tuple(vals),
        )

    run_db(_write)


@with_conn


def upsert_tournament(guild_id: int, data: Dict[str, Any]) -> int:
    """Compatibility wrapper for older cogs.

    If an active tournament exists for this guild, update its core settings.
    Otherwise create a new tournament.

    Returns the tournament_id.
    """
    from core.db import get_db_connection, run_db  # local import to avoid cycles

    name = str(data.get("name") or "Tournament").strip() or "Tournament"
    max_teams = int(data.get("max_teams") or 8)
    best_of = int(data.get("best_of") or 3)
    team_size = int(data.get("team_size") or 2)

    with get_db_connection() as conn:
        def _op(c):
            t = get_tournament(c, guild_id)
            if not t:
                return create_tournament(
                    c,
                    guild_id=guild_id,
                    name=name,
                    max_teams=max_teams,
                    best_of=best_of,
                    team_size=team_size,
                    join_open=True,
                    open_join_mode=True,
                )

            now = _now()
            c.execute(
                """
                UPDATE tournaments
                   SET name = ?,
                       max_teams = ?,
                       best_of = ?,
                       team_size = ?,
                       updated_at = ?
                 WHERE tournament_id = ?
                """,
                (name, max_teams, best_of, team_size, now, int(t["tournament_id"])),
            )
            return int(t["tournament_id"])

        return run_db(conn, _op)


def delete_tournament(conn: sqlite3.Connection, tournament_id: int) -> None:
    """
    DB-side delete. Discord channel/role deletes are in your bot code.
    """
    def _write():
        _begin(conn)
        try:
            conn.execute(
                "UPDATE tournaments SET status = 'deleted', updated_at = ? WHERE tournament_id = ?",
                (_now(), tournament_id),
            )
            conn.execute("DELETE FROM tournaments WHERE tournament_id = ?", (tournament_id,))
            _commit(conn)
        except Exception:
            _rollback(conn)
            raise

    run_db(_write)


# -----------------------------
# Participants (players + bots)
# -----------------------------
@with_conn
def upsert_participant(
    conn: sqlite3.Connection,
    guild_id: int,
    user_id: int,
    display_name: Optional[str] = None,
    is_bot: bool = False,
) -> None:
    now = _now()

    def _write():
        conn.execute(
            """
            INSERT INTO participants (guild_id, user_id, is_bot, display_name, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(guild_id, user_id) DO UPDATE SET
                is_bot = excluded.is_bot,
                display_name = COALESCE(excluded.display_name, participants.display_name),
                updated_at = excluded.updated_at
            """,
            (guild_id, user_id, int(is_bot), display_name, now, now),
        )

    run_db(_write)


@with_conn
def join_tournament(conn: sqlite3.Connection, tournament_id: int, guild_id: int, user_id: int) -> None:
    now = _now()

    def _write():
        _begin(conn)
        try:
            conn.execute(
                """
                INSERT OR IGNORE INTO tournament_participants (tournament_id, guild_id, user_id, joined_at)
                VALUES (?, ?, ?, ?)
                """,
                (tournament_id, guild_id, user_id, now),
            )
            conn.execute("UPDATE tournaments SET updated_at = ? WHERE tournament_id = ?", (now, tournament_id))
            _commit(conn)
        except Exception:
            _rollback(conn)
            raise

    run_db(_write)


@with_conn
def remove_from_tournament(conn: sqlite3.Connection, tournament_id: int, user_id: int) -> None:
    def _write():
        _begin(conn)
        try:
            conn.execute("DELETE FROM team_members WHERE tournament_id = ? AND user_id = ?", (tournament_id, user_id))
            conn.execute("DELETE FROM tournament_participants WHERE tournament_id = ? AND user_id = ?", (tournament_id, user_id))
            conn.execute("UPDATE tournaments SET updated_at = ? WHERE tournament_id = ?", (_now(), tournament_id))
            _commit(conn)
        except Exception:
            _rollback(conn)
            raise

    run_db(_write)


@with_conn
def list_tournament_participants(conn: sqlite3.Connection, tournament_id: int) -> List[Dict[str, Any]]:
    rows = _fetchall(
        conn,
        """
        SELECT tp.user_id, tp.joined_at, p.is_bot, p.display_name
        FROM tournament_participants tp
        JOIN participants p ON p.guild_id = tp.guild_id AND p.user_id = tp.user_id
        WHERE tp.tournament_id = ?
        ORDER BY tp.joined_at ASC
        """,
        (tournament_id,),
    )
    return [dict(r) for r in rows]


# -----------------------------
# Teams
# -----------------------------
@with_conn
def user_team_id(conn: sqlite3.Connection, tournament_id: int, user_id: int) -> Optional[int]:
    row = _fetchone(
        conn,
        "SELECT team_id FROM team_members WHERE tournament_id = ? AND user_id = ? LIMIT 1",
        (tournament_id, user_id),
    )
    return int(row["team_id"]) if row else None


@with_conn
def create_team(
    conn: sqlite3.Connection,
    tournament_id: int,
    guild_id: int,
    name: str,
    captain_user_id: int,
    is_bot_team: bool = False,
) -> int:
    now = _now()

    def _write() -> int:
        _begin(conn)
        try:
            cur = conn.execute(
                """
                INSERT INTO teams
                (tournament_id, guild_id, name, captain_user_id, is_bot_team, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (tournament_id, guild_id, name, captain_user_id, int(is_bot_team), now, now),
            )
            team_id = int(cur.lastrowid)
            conn.execute("UPDATE tournaments SET updated_at = ? WHERE tournament_id = ?", (now, tournament_id))
            _commit(conn)
            return team_id
        except Exception:
            _rollback(conn)
            raise

    return run_db(_write)


@with_conn
def set_team_discord_refs(
    conn: sqlite3.Connection,
    team_id: int,
    role_id: Optional[int] = None,
    hub_channel_id: Optional[int] = None,
) -> None:
    sets: List[str] = []
    vals: List[Any] = []
    if role_id is not None:
        sets.append("role_id = ?")
        vals.append(role_id)
    if hub_channel_id is not None:
        sets.append("hub_channel_id = ?")
        vals.append(hub_channel_id)
    if not sets:
        return

    vals.append(_now())
    vals.append(team_id)

    def _write():
        conn.execute(
            f"UPDATE teams SET {', '.join(sets)}, updated_at = ? WHERE team_id = ?",
            tuple(vals),
        )

    run_db(_write)


@with_conn
def add_team_member(conn: sqlite3.Connection, tournament_id: int, team_id: int, guild_id: int, user_id: int) -> None:
    now = _now()

    def _write():
        _begin(conn)
        try:
            # Ensure tournament membership exists
            conn.execute(
                "INSERT OR IGNORE INTO tournament_participants (tournament_id, guild_id, user_id, joined_at) VALUES (?, ?, ?, ?)",
                (tournament_id, guild_id, user_id, now),
            )

            # Enforce one-team-per-user (PRIMARY KEY (tournament_id, user_id))
            conn.execute(
                "INSERT INTO team_members (tournament_id, team_id, guild_id, user_id, joined_at) VALUES (?, ?, ?, ?, ?)",
                (tournament_id, team_id, guild_id, user_id, now),
            )

            conn.execute("UPDATE teams SET updated_at = ? WHERE team_id = ?", (now, team_id))
            conn.execute("UPDATE tournaments SET updated_at = ? WHERE tournament_id = ?", (now, tournament_id))
            _commit(conn)
        except Exception:
            _rollback(conn)
            raise

    run_db(_write)


@with_conn
def remove_team_member(conn: sqlite3.Connection, tournament_id: int, user_id: int) -> None:
    now = _now()

    def _write():
        _begin(conn)
        try:
            row = _fetchone(conn, "SELECT team_id FROM team_members WHERE tournament_id = ? AND user_id = ?", (tournament_id, user_id))
            conn.execute("DELETE FROM team_members WHERE tournament_id = ? AND user_id = ?", (tournament_id, user_id))
            if row:
                conn.execute("UPDATE teams SET updated_at = ? WHERE team_id = ?", (now, int(row["team_id"])))
            conn.execute("UPDATE tournaments SET updated_at = ? WHERE tournament_id = ?", (now, tournament_id))
            _commit(conn)
        except Exception:
            _rollback(conn)
            raise

    run_db(_write)


@with_conn
def set_team_ready(conn: sqlite3.Connection, team_id: int, ready: bool) -> None:
    def _write():
        conn.execute("UPDATE teams SET ready = ?, updated_at = ? WHERE team_id = ?", (int(ready), _now(), team_id))
    run_db(_write)


@with_conn
def delete_team(conn: sqlite3.Connection, team_id: int) -> None:
    def _write():
        _begin(conn)
        try:
            conn.execute("DELETE FROM teams WHERE team_id = ?", (team_id,))
            _commit(conn)
        except Exception:
            _rollback(conn)
            raise
    run_db(_write)


@with_conn
def list_teams(conn: sqlite3.Connection, tournament_id: int) -> List[Dict[str, Any]]:
    return [dict(r) for r in _fetchall(conn, "SELECT * FROM teams WHERE tournament_id = ? ORDER BY created_at ASC", (tournament_id,))]


@with_conn
def get_team(conn: sqlite3.Connection, team_id: int) -> Optional[Dict[str, Any]]:
    return _dict(_fetchone(conn, "SELECT * FROM teams WHERE team_id = ? LIMIT 1", (team_id,)))


@with_conn
def team_members(conn: sqlite3.Connection, tournament_id: int, team_id: int) -> List[Dict[str, Any]]:
    rows = _fetchall(
        conn,
        """
        SELECT tm.user_id, tm.joined_at, p.is_bot, p.display_name
        FROM team_members tm
        JOIN participants p ON p.guild_id = tm.guild_id AND p.user_id = tm.user_id
        WHERE tm.tournament_id = ? AND tm.team_id = ?
        ORDER BY tm.joined_at ASC
        """,
        (tournament_id, team_id),
    )
    return [dict(r) for r in rows]


@with_conn
def count_team_members(conn: sqlite3.Connection, tournament_id: int, team_id: int) -> int:
    row = _fetchone(conn, "SELECT COUNT(*) AS c FROM team_members WHERE tournament_id = ? AND team_id = ?", (tournament_id, team_id))
    return int(row["c"]) if row else 0


@with_conn
def list_ready_teams(conn: sqlite3.Connection, tournament_id: int) -> List[Dict[str, Any]]:
    return [dict(r) for r in _fetchall(conn, "SELECT * FROM teams WHERE tournament_id = ? AND ready = 1 ORDER BY created_at ASC", (tournament_id,))]


# -----------------------------
# Bracket
# -----------------------------
@with_conn
def clear_bracket(conn: sqlite3.Connection, tournament_id: int) -> None:
    def _write():
        _begin(conn)
        try:
            conn.execute("DELETE FROM bracket_matches WHERE tournament_id = ?", (tournament_id,))
            conn.execute("UPDATE tournaments SET updated_at = ? WHERE tournament_id = ?", (_now(), tournament_id))
            _commit(conn)
        except Exception:
            _rollback(conn)
            raise
    run_db(_write)


@with_conn
def insert_bracket_match(
    conn: sqlite3.Connection,
    tournament_id: int,
    round_no: int,
    match_no: int,
    team_a_id: Optional[int],
    team_b_id: Optional[int],
    status: str = "pending",
    match_channel_id: Optional[int] = None,
) -> int:
    now = _now()

    def _write() -> int:
        _begin(conn)
        try:
            cur = conn.execute(
                """
                INSERT INTO bracket_matches
                (tournament_id, round_no, match_no, team_a_id, team_b_id, status, match_channel_id, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (tournament_id, round_no, match_no, team_a_id, team_b_id, status, match_channel_id, now, now),
            )
            mid = int(cur.lastrowid)
            conn.execute("UPDATE tournaments SET updated_at = ? WHERE tournament_id = ?", (now, tournament_id))
            _commit(conn)
            return mid
        except Exception:
            _rollback(conn)
            raise

    return run_db(_write)


@with_conn
def update_bracket_match_score(
    conn: sqlite3.Connection,
    match_id: int,
    score_a: int,
    score_b: int,
    winner_team_id: Optional[int],
    status: str = "complete",
) -> None:
    def _write():
        conn.execute(
            """
            UPDATE bracket_matches
            SET score_a = ?, score_b = ?, winner_team_id = ?, status = ?, updated_at = ?
            WHERE match_id = ?
            """,
            (score_a, score_b, winner_team_id, status, _now(), match_id),
        )
    run_db(_write)


@with_conn
def set_bracket_match_channel(conn: sqlite3.Connection, match_id: int, match_channel_id: Optional[int]) -> None:
    def _write():
        conn.execute(
            "UPDATE bracket_matches SET match_channel_id = ?, updated_at = ? WHERE match_id = ?",
            (match_channel_id, _now(), match_id),
        )
    run_db(_write)


@with_conn
def get_bracket_matches(conn: sqlite3.Connection, tournament_id: int) -> List[Dict[str, Any]]:
    rows = _fetchall(
        conn,
        """
        SELECT *
        FROM bracket_matches
        WHERE tournament_id = ?
        ORDER BY round_no ASC, match_no ASC
        """,
        (tournament_id,),
    )
    return [dict(r) for r in rows]


@with_conn
def get_match(conn: sqlite3.Connection, match_id: int) -> Optional[Dict[str, Any]]:
    return _dict(_fetchone(conn, "SELECT * FROM bracket_matches WHERE match_id = ? LIMIT 1", (match_id,)))


# -----------------------------
# Action log (debug/admin)
# -----------------------------
@with_conn
def log_action(conn: sqlite3.Connection, guild_id: int, action: str, tournament_id: Optional[int] = None, details_json: Optional[str] = None) -> None:
    def _write():
        conn.execute(
            "INSERT INTO action_log (guild_id, tournament_id, action, details_json, created_at) VALUES (?, ?, ?, ?, ?)",
            (guild_id, tournament_id, action, details_json, _now()),
        )
    run_db(_write)


@with_conn
def recent_actions(conn: sqlite3.Connection, guild_id: int, limit: int = 25) -> List[Dict[str, Any]]:
    rows = _fetchall(
        conn,
        "SELECT * FROM action_log WHERE guild_id = ? ORDER BY created_at DESC LIMIT ?",
        (guild_id, int(limit)),
    )
    return [dict(r) for r in rows]


# -----------------------------
# Quick UI counts
# -----------------------------
@with_conn
def tournament_counts(conn: sqlite3.Connection, tournament_id: int) -> Dict[str, int]:
    row1 = _fetchone(conn, "SELECT COUNT(*) AS c FROM tournament_participants WHERE tournament_id = ?", (tournament_id,))
    row2 = _fetchone(conn, "SELECT COUNT(*) AS c FROM teams WHERE tournament_id = ?", (tournament_id,))
    row3 = _fetchone(conn, "SELECT COUNT(*) AS c FROM teams WHERE tournament_id = ? AND ready = 1", (tournament_id,))
    return {
        "participants": int(row1["c"]) if row1 else 0,
        "teams": int(row2["c"]) if row2 else 0,
        "ready_teams": int(row3["c"]) if row3 else 0,
    }


@with_conn
def is_user_in_tournament(conn: sqlite3.Connection, tournament_id: int, user_id: int) -> bool:
    return _fetchone(conn, "SELECT 1 FROM tournament_participants WHERE tournament_id = ? AND user_id = ? LIMIT 1", (tournament_id, user_id)) is not None


@with_conn
def is_user_in_team(conn: sqlite3.Connection, tournament_id: int, user_id: int) -> bool:
    return _fetchone(conn, "SELECT 1 FROM team_members WHERE tournament_id = ? AND user_id = ? LIMIT 1", (tournament_id, user_id)) is not None

# ---------- Compatibility helpers (legacy API) ----------
def get_db_connection() -> sqlite3.Connection:
    """Legacy helper kept for older cogs. Prefer core.db.get_db_connection."""
    return core_db.get_db_connection()
def get_ready_teams(guild_id: int):
    """Legacy alias for list_ready_teams."""
    return list_ready_teams(guild_id)
def update_bracket_match(
    guild_id: int,
    match_id: int,
    *,
    winner: int | None = None,
    status: str | None = None,
    score_a: int | None = None,
    score_b: int | None = None,
    match_channel_id: int | None = None,
) -> None:
    """Legacy updater used by older bracket cogs."""
    fields = []
    params: list[Any] = []

    if winner is not None:
        fields.append("winner_team_id = ?")
        params.append(winner)
    if status is not None:
        fields.append("status = ?")
        params.append(status.lower() if isinstance(status, str) else status)
    if score_a is not None:
        fields.append("score_a = ?")
        params.append(score_a)
    if score_b is not None:
        fields.append("score_b = ?")
        params.append(score_b)
    if match_channel_id is not None:
        fields.append("match_channel_id = ?")
        params.append(match_channel_id)

    if not fields:
        return

    fields.append("updated_at = ?")
    params.append(_now())
    params.extend([guild_id, match_id])

    sql = f"UPDATE bracket_matches SET {', '.join(fields)} WHERE tournament_id = ? AND match_id = ?"
    core_db.run_db(sql, tuple(params))
async def setup(bot):
    """Allow this module to be safely loaded as an extension by the auto-loader."""
    return
