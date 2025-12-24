# cogs/tournament_db.py
import sqlite3
from pathlib import Path
from typing import Optional, Dict, Any, List

from discord.ext import commands

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "tournaments.db"


# ---------- DB Helpers ----------

def get_db_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    conn = get_db_connection()
    cur = conn.cursor()

    # ---- Tournaments table ----
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS tournaments (
            guild_id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            max_teams INTEGER NOT NULL,
            team_size INTEGER NOT NULL,
            best_of INTEGER NOT NULL,
            bracket_type TEXT NOT NULL,
            captain_scoring INTEGER NOT NULL,
            screenshot_proof INTEGER NOT NULL,
            queue_status TEXT NOT NULL,
            status TEXT NOT NULL,
            panel_channel_id INTEGER,
            panel_message_id INTEGER,
            teams_joined INTEGER NOT NULL DEFAULT 0,
            category_id INTEGER,
            player_role_id INTEGER,
            spectator_role_id INTEGER,
            players_joined INTEGER NOT NULL DEFAULT 0,
            spectators_joined INTEGER NOT NULL DEFAULT 0,
            join_panel_channel_id INTEGER,
            join_panel_message_id INTEGER,
            join_invite_code TEXT
        )
        """
    )

    # Migration checks for tournaments
    cur.execute("PRAGMA table_info(tournaments)")
    cols = [row[1] for row in cur.fetchall()]

    def ensure_tournament_column(name: str, ddl: str) -> None:
        if name not in cols:
            cur.execute(ddl)

    ensure_tournament_column("category_id", "ALTER TABLE tournaments ADD COLUMN category_id INTEGER")
    ensure_tournament_column("player_role_id", "ALTER TABLE tournaments ADD COLUMN player_role_id INTEGER")
    ensure_tournament_column("spectator_role_id", "ALTER TABLE tournaments ADD COLUMN spectator_role_id INTEGER")
    ensure_tournament_column("players_joined", "ALTER TABLE tournaments ADD COLUMN players_joined INTEGER NOT NULL DEFAULT 0")
    ensure_tournament_column("spectators_joined", "ALTER TABLE tournaments ADD COLUMN spectators_joined INTEGER NOT NULL DEFAULT 0")
    ensure_tournament_column("join_panel_channel_id", "ALTER TABLE tournaments ADD COLUMN join_panel_channel_id INTEGER")
    ensure_tournament_column("join_panel_message_id", "ALTER TABLE tournaments ADD COLUMN join_panel_message_id INTEGER")
    ensure_tournament_column("join_invite_code", "ALTER TABLE tournaments ADD COLUMN join_invite_code TEXT")

    # ---- TEAMS TABLE ----
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS teams (
            guild_id INTEGER NOT NULL,
            team_id INTEGER PRIMARY KEY AUTOINCREMENT,
            team_name TEXT NOT NULL,
            role_id INTEGER NOT NULL,
            captain_id INTEGER NOT NULL,
            is_ready INTEGER NOT NULL DEFAULT 0,
            is_bot INTEGER NOT NULL DEFAULT 0
        )
        """
    )

    # Migration checks for teams (minimal change: add is_bot if missing)
    cur.execute("PRAGMA table_info(teams)")
    team_cols = [row[1] for row in cur.fetchall()]

    if "is_bot" not in team_cols:
        cur.execute("ALTER TABLE teams ADD COLUMN is_bot INTEGER NOT NULL DEFAULT 0")

    # ---- Bracket matches ----
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS bracket_matches (
            guild_id INTEGER NOT NULL,
            match_id INTEGER NOT NULL,
            round_number INTEGER NOT NULL,
            team_a TEXT,
            team_b TEXT,
            winner TEXT,
            status TEXT NOT NULL,
            channel_id INTEGER,
            PRIMARY KEY (guild_id, match_id)
        )
        """
    )

    conn.commit()
    conn.close()


# ---------- Tournament Basic Queries ----------

def get_tournament(guild_id: int) -> Optional[Dict[str, Any]]:
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM tournaments WHERE guild_id = ?", (guild_id,))
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def upsert_tournament(guild_id: int, data: Dict[str, Any]) -> None:
    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("SELECT guild_id FROM tournaments WHERE guild_id = ?", (guild_id,))
    exists = cur.fetchone() is not None

    if exists:
        cur.execute(
            """
            UPDATE tournaments
            SET name = ?, max_teams = ?, team_size = ?, best_of = ?,
                bracket_type = ?, captain_scoring = ?, screenshot_proof = ?,
                queue_status = ?, status = ?, panel_channel_id = ?,
                panel_message_id = ?, teams_joined = ?, category_id = ?,
                player_role_id = ?, spectator_role_id = ?, players_joined = ?,
                spectators_joined = ?, join_panel_channel_id = ?,
                join_panel_message_id = ?, join_invite_code = ?
            WHERE guild_id = ?
            """,
            (
                data["name"], data["max_teams"], data["team_size"], data["best_of"],
                data["bracket_type"], int(data["captain_scoring"]),
                int(data["screenshot_proof"]), data["queue_status"], data["status"],
                data.get("panel_channel_id"), data.get("panel_message_id"),
                data.get("teams_joined", 0), data.get("category_id"),
                data.get("player_role_id"), data.get("spectator_role_id"),
                data.get("players_joined", 0), data.get("spectators_joined", 0),
                data.get("join_panel_channel_id"), data.get("join_panel_message_id"),
                data.get("join_invite_code"), guild_id,
            ),
        )
    else:
        cur.execute(
            """
            INSERT INTO tournaments (
                guild_id, name, max_teams, team_size, best_of,
                bracket_type, captain_scoring, screenshot_proof,
                queue_status, status, panel_channel_id, panel_message_id,
                teams_joined, category_id, player_role_id, spectator_role_id,
                players_joined, spectators_joined, join_panel_channel_id,
                join_panel_message_id, join_invite_code
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                guild_id, data["name"], data["max_teams"], data["team_size"], data["best_of"],
                data["bracket_type"], int(data["captain_scoring"]),
                int(data["screenshot_proof"]), data["queue_status"], data["status"],
                data.get("panel_channel_id"), data.get("panel_message_id"),
                data.get("teams_joined", 0), data.get("category_id"),
                data.get("player_role_id"), data.get("spectator_role_id"),
                data.get("players_joined", 0), data.get("spectators_joined", 0),
                data.get("join_panel_channel_id"), data.get("join_panel_message_id"),
                data.get("join_invite_code"),
            ),
        )

    conn.commit()
    conn.close()


def delete_tournament(guild_id: int) -> None:
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM tournaments WHERE guild_id = ?", (guild_id,))
    cur.execute("DELETE FROM teams WHERE guild_id = ?", (guild_id,))
    cur.execute("DELETE FROM bracket_matches WHERE guild_id = ?", (guild_id,))
    conn.commit()
    conn.close()


# ---------- TEAM HELPERS ----------

def add_team(
    guild_id: int,
    team_name: str,
    role_id: int,
    captain_id: int,
    *,
    is_bot: bool = False,
) -> None:
    """
    Add a team to the DB.
    is_bot = False for normal teams, True for bot teams.
    From the DB's point of view they are treated the same.
    """
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO teams (guild_id, team_name, role_id, captain_id, is_ready, is_bot)
        VALUES (?, ?, ?, ?, 0, ?)
        """,
        (guild_id, team_name, role_id, captain_id, 1 if is_bot else 0),
    )
    conn.commit()
    conn.close()


def delete_team(guild_id: int, role_id: int) -> None:
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM teams WHERE guild_id = ? AND role_id = ?", (guild_id, role_id))
    conn.commit()
    conn.close()


def set_team_ready(guild_id: int, role_id: int, is_ready: bool) -> None:
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "UPDATE teams SET is_ready = ? WHERE guild_id = ? AND role_id = ?",
        (1 if is_ready else 0, guild_id, role_id),
    )
    conn.commit()
    conn.close()


def get_ready_teams(guild_id: int) -> List[sqlite3.Row]:
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM teams WHERE guild_id = ? AND is_ready = 1 ORDER BY team_name ASC",
        (guild_id,),
    )
    rows = cur.fetchall()
    conn.close()
    return rows


def get_all_teams(guild_id: int) -> List[sqlite3.Row]:
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM teams WHERE guild_id = ? ORDER BY team_name ASC",
        (guild_id,),
    )
    rows = cur.fetchall()
    conn.close()
    return rows


# ---------- Bracket helpers ----------

def clear_bracket(guild_id: int) -> None:
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM bracket_matches WHERE guild_id = ?", (guild_id,))
    conn.commit()
    conn.close()


def get_bracket_matches(guild_id: int) -> List[sqlite3.Row]:
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM bracket_matches WHERE guild_id = ? ORDER BY round_number ASC, match_id ASC",
        (guild_id,),
    )
    rows = cur.fetchall()
    conn.close()
    return rows


def insert_bracket_match(
    guild_id: int,
    match_id: int,
    round_number: int,
    team_a: Optional[str],
    team_b: Optional[str],
    status: str = "pending",
    winner: Optional[str] = None,
    channel_id: Optional[int] = None,
) -> None:
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT OR REPLACE INTO bracket_matches (
            guild_id, match_id, round_number, team_a, team_b,
            winner, status, channel_id
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (guild_id, match_id, round_number, team_a, team_b, winner, status, channel_id),
    )
    conn.commit()
    conn.close()


def update_bracket_match(
    guild_id: int,
    match_id: int,
    *,
    team_a: Optional[str] = None,
    team_b: Optional[str] = None,
    winner: Optional[str] = None,
    status: Optional[str] = None,
    channel_id: Optional[int] = None,
) -> None:
    fields: List[str] = []
    values: List[Any] = []

    if team_a is not None:
        fields.append("team_a = ?")
        values.append(team_a)
    if team_b is not None:
        fields.append("team_b = ?")
        values.append(team_b)
    if winner is not None:
        fields.append("winner = ?")
        values.append(winner)
    if status is not None:
        fields.append("status = ?")
        values.append(status)
    if channel_id is not None:
        fields.append("channel_id = ?")
        values.append(channel_id)

    if not fields:
        return

    conn = get_db_connection()
    cur = conn.cursor()
    values.append(guild_id)
    values.append(match_id)
    cur.execute(
        f"UPDATE bracket_matches SET {', '.join(fields)} WHERE guild_id = ? AND match_id = ?",
        values,
    )
    conn.commit()
    conn.close()


# ---------- REQUIRED FOR AUTO-LOADER ----------

async def setup(bot: commands.Bot) -> None:
    # Ensure DB, migrations, and all tournament tables exist
    init_db()
