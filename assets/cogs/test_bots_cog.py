# cogs/bots_testing_cog.py
import logging
import sqlite3
from typing import Optional, Dict, Any, List, Tuple

import discord
from discord import app_commands
from discord.ext import commands

from .tournament_db import get_db_connection, get_tournament  # your DB helper

log = logging.getLogger(__name__)


def ensure_bot_table():
    """Ensure the bot_players table exists."""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS bot_players (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            label TEXT NOT NULL
        )
        """
    )
    conn.commit()
    conn.close()


def fetch_bots_for_guild(guild_id: int) -> List[sqlite3.Row]:
    """Return all bot players for this guild."""
    ensure_bot_table()
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT id, guild_id, label FROM bot_players WHERE guild_id = ? ORDER BY id ASC",
        (guild_id,),
    )
    rows = cur.fetchall()
    conn.close()
    return rows


class BotsTestingCog(commands.Cog):
    """
    /bots add <count>      -> add N bot "players" into DB
    /bots force_teams      -> turn bots into real teams based on team_size & max_teams
    /bots clear            -> clear bot teams, roles, channels, and bot players
    """

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        ensure_bot_table()

    bots = app_commands.Group(
        name="bots",
        description="Testing helpers: generate and manage bot players/teams."
    )

    # ------------- /bots add -------------

    @bots.command(
        name="add",
        description="Add bot PLAYERS (not teams). Example: /bots add 16 for 16 bots."
    )
    @app_commands.describe(
        count="How many bot PLAYERS to add for this server."
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def bots_add(
        self,
        interaction: discord.Interaction,
        count: app_commands.Range[int, 1, 128] = 8,
    ):
        await interaction.response.defer(ephemeral=True, thinking=True)

        guild = interaction.guild
        if guild is None:
            await interaction.followup.send(
                "This command can only be used in a server.",
                ephemeral=True,
            )
            return

        ensure_bot_table()
        conn = get_db_connection()
        cur = conn.cursor()

        # how many bots already exist for this guild
        cur.execute(
            "SELECT COUNT(*) FROM bot_players WHERE guild_id = ?",
            (guild.id,),
        )
        existing = cur.fetchone()[0] or 0

        new_labels: List[str] = []
        try:
            for i in range(count):
                label = f"Bot #{existing + i + 1}"
                cur.execute(
                    "INSERT INTO bot_players (guild_id, label) VALUES (?, ?)",
                    (guild.id, label),
                )
                new_labels.append(label)
            conn.commit()
        except Exception as e:
            conn.rollback()
            log.exception("DB error in /bots add: %s", e)
            await interaction.followup.send(
                "❌ There was a database error while adding bots.",
                ephemeral=True,
            )
            return
        finally:
            conn.close()

        lines = [
            f"✅ Added **{len(new_labels)}** bot players for this server.",
            "",
            "**New Bots:**",
        ]
        for lbl in new_labels:
            lines.append(f"• {lbl}")

        await interaction.followup.send("\n".join(lines), ephemeral=True)

    # ------------- /bots force_teams -------------

    @bots.command(
        name="force_teams",
        description="Use stored bots to auto-create teams based on team_size / max_teams."
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def bots_force_teams(self, interaction: discord.Interaction):
        """
        Example:
        - Tournament team_size = 2
        - max_teams = 8
        - You ran /bots add 16

        => /bots force_teams will:
           - Make 8 teams
           - Each team gets 2 bots
           - Creates team roles + channels
           - Posts which bots are in each team
           - Updates tournaments.teams_joined
           - Removes used bots from bot_players
        """
        await interaction.response.defer(ephemeral=True, thinking=True)

        guild = interaction.guild
        if guild is None:
            await interaction.followup.send(
                "This command can only be used in a server.",
                ephemeral=True,
            )
            return

        t_data: Optional[Dict[str, Any]] = get_tournament(guild.id)
        if not t_data:
            await interaction.followup.send(
                "❌ There is **no tournament record** for this server.\n"
                "Create one with your tournament setup command first.",
                ephemeral=True,
            )
            return

        team_size = t_data.get("team_size") or 1
        max_teams = t_data.get("max_teams") or 0
        teams_joined = t_data.get("teams_joined", 0) or 0

        bots = fetch_bots_for_guild(guild.id)
        if not bots:
            await interaction.followup.send(
                "⚠️ There are **no bot players** stored for this server.\n"
                "Use `/bots add <count>` first.",
                ephemeral=True,
            )
            return

        # how many teams can we form from bots
        num_possible_teams_from_bots = len(bots) // team_size
        slots_left = max(0, max_teams - teams_joined) if max_teams > 0 else num_possible_teams_from_bots
        teams_to_create = min(num_possible_teams_from_bots, slots_left)

        if teams_to_create <= 0:
            await interaction.followup.send(
                "❌ No teams can be created.\n"
                f"- Bots available: `{len(bots)}`\n"
                f"- team_size: `{team_size}`\n"
                f"- max_teams: `{max_teams}`\n"
                f"- teams_joined: `{teams_joined}`",
                ephemeral=True,
            )
            return

        # ---------- get / create DEDICATED TEAMS CATEGORY ----------
        teams_category: Optional[discord.CategoryChannel] = None

        # Try to find existing "Tournament Teams" category
        for cat in guild.categories:
            name_lower = cat.name.lower()
            if "tournament" in name_lower and "team" in name_lower:
                teams_category = cat
                break

        if teams_category is None:
            # Try to position near the main tournament category (if we have one)
            base_category: Optional[discord.CategoryChannel] = None
            category_id = t_data.get("category_id")
            if category_id:
                ch = guild.get_channel(category_id)
                if isinstance(ch, discord.CategoryChannel):
                    base_category = ch

            teams_category = await guild.create_category(
                name="🛡 Tournament Teams",
                reason="[BotsTestingCog] Auto-created teams category for bot teams",
            )

            if base_category is not None:
                try:
                    await teams_category.move(after=base_category)
                except Exception:
                    pass

        category: Optional[discord.CategoryChannel] = teams_category

        created_teams: List[Tuple[str, discord.Role, discord.TextChannel, List[str]]] = []
        used_bot_ids: List[int] = []

        everyone = guild.default_role
        tournament_admin_role = None  # set this if you have a specific staff role

        # group bots into teams
        for t_idx in range(teams_to_create):
            # slice bots for this team
            start = t_idx * team_size
            end = start + team_size
            group = bots[start:end]
            if not group:
                break

            # team index based on teams_joined
            team_number = teams_joined + t_idx + 1
            team_name = f"Bot Team {team_number}"
            role_name = team_name
            channel_name = f"team-bot-{team_number}"

            # collect labels and ids
            bot_labels = [row["label"] for row in group]
            bot_ids = [row["id"] for row in group]
            used_bot_ids.extend(bot_ids)

            # ---- create role ----
            role = await guild.create_role(
                name=role_name,
                mentionable=False,
                reason=f"[BotsTestingCog] Creating forced bot team for guild {guild.id}",
            )

            # ---- channel overwrites ----
            overwrites = {
                everyone: discord.PermissionOverwrite(view_channel=False),
                role: discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=True,
                    read_message_history=True,
                ),
                guild.me: discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=True,
                    manage_channels=True,
                    manage_messages=True,
                    read_message_history=True,
                ),
            }
            if tournament_admin_role:
                overwrites[tournament_admin_role] = discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=True,
                    read_message_history=True,
                    manage_messages=True,
                )

            # ---- create team hub channel IN TEAMS CATEGORY ----
            channel_kwargs = {
                "name": channel_name,
                "overwrites": overwrites,
                "reason": f"[BotsTestingCog] Creating forced bot team channel for guild {guild.id}",
            }
            if category is not None:
                channel = await category.create_text_channel(**channel_kwargs)
            else:
                channel = await guild.create_text_channel(**channel_kwargs)

            # Post which bots are on this team
            bot_list_text = "\n".join(f"- {label}" for label in bot_labels)
            await channel.send(
                f"🤖 **Forced Bot Team {team_number}**\n"
                f"Team size: `{team_size}`\n"
                f"Bot players:\n{bot_list_text}"
            )

            created_teams.append((team_name, role, channel, bot_labels))

        # ---- update DB: teams_joined and teams table + remove used bots ----
        conn = get_db_connection()
        cur = conn.cursor()
        new_teams_joined = teams_joined + len(created_teams)
        try:
            # update tournaments table
            cur.execute(
                "UPDATE tournaments SET teams_joined = ? WHERE guild_id = ?",
                (new_teams_joined, guild.id),
            )

            # Insert bot teams into the REAL teams table
            # Schema: guild_id, team_id (auto), team_name, role_id, captain_id, is_ready
            for team_name, role, channel, _ in created_teams:
                try:
                    cur.execute(
                        """
                        INSERT INTO teams (
                            guild_id,
                            team_name,
                            role_id,
                            captain_id,
                            is_ready
                        ) VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            guild.id,
                            team_name,
                            role.id,
                            interaction.user.id,  # treat the command user as "captain"
                            1,  # mark bot teams as READY in DB
                        ),
                    )
                except sqlite3.OperationalError as e:
                    # If teams table somehow missing, log it (should not happen)
                    log.exception("Error inserting bot team into teams table: %s", e)

            # delete used bots from bot_players
            if used_bot_ids:
                q_marks = ",".join("?" for _ in used_bot_ids)
                cur.execute(
                    f"DELETE FROM bot_players WHERE id IN ({q_marks})",
                    used_bot_ids,
                )

            conn.commit()
        except Exception as e:
            conn.rollback()
            log.exception("DB error in /bots force_teams: %s", e)
        finally:
            conn.close()

        # refresh panels if helpers exist
        t_cog = self.bot.get_cog("TournamentCog")
        if t_cog is not None:
            try:
                refresh_admin = getattr(t_cog, "refresh_admin_panels", None)
                if callable(refresh_admin):
                    await refresh_admin(guild)

                refresh_join = getattr(t_cog, "refresh_join_panels", None)
                if callable(refresh_join):
                    await refresh_join(guild)
            except Exception as e:
                log.exception("Error refreshing panels after /bots force_teams: %s", e)

        # reply
        lines = [
            f"✅ Forced **{len(created_teams)}** bot teams using stored bots.",
            f"- team_size: `{team_size}`",
            f"- max_teams: `{max_teams}`",
            f"- teams_joined (new): `{new_teams_joined}`",
            "",
            "**Created Teams:**",
        ]
        for team_name, role, channel, bot_labels in created_teams:
            bot_line = ", ".join(bot_labels)
            lines.append(f"• {team_name} — {role.mention} — {channel.mention} ({bot_line})")

        # If this ever hits 2000 chars we might need to trim, but keeping your behavior for now
        text = "\n".join(lines)
        if len(text) > 2000:
            text = text[:1990] + "\n…(trimmed)"
        await interaction.followup.send(text, ephemeral=True)

    # ------------- /bots clear -------------

    @bots.command(
        name="clear",
        description="Delete bot teams (channels + roles) and all stored bot players."
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def bots_clear(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)

        guild = interaction.guild
        if guild is None:
            await interaction.followup.send(
                "This command can only be used in a server.",
                ephemeral=True,
            )
            return

        t_data: Optional[Dict[str, Any]] = get_tournament(guild.id)
        teams_joined = t_data.get("teams_joined", 0) if t_data else 0

        deleted_teams = 0

        # delete team-bot-* channels
        for ch in guild.channels:
            if isinstance(ch, discord.TextChannel) and ch.name.startswith("team-bot"):
                try:
                    await ch.delete(reason="[BotsTestingCog] Clearing bot teams")
                    deleted_teams += 1
                except Exception:
                    pass

        # delete roles named "Bot Team X"
        for role in guild.roles:
            if role.name.startswith("Bot Team "):
                try:
                    await role.delete(reason="[BotsTestingCog] Clearing bot teams")
                except Exception:
                    pass

        # DB cleanup
        conn = get_db_connection()
        cur = conn.cursor()
        try:
            # delete bot teams from teams table by name pattern (bots are stored as normal teams)
            try:
                cur.execute(
                    "DELETE FROM teams WHERE guild_id = ? AND team_name LIKE 'Bot Team %'",
                    (guild.id,),
                )
            except sqlite3.OperationalError:
                # teams table missing would be unexpected, but don't crash
                pass

            # delete all bot_players for this guild
            ensure_bot_table()
            cur.execute(
                "DELETE FROM bot_players WHERE guild_id = ?",
                (guild.id,),
            )

            # adjust tournaments.teams_joined
            if t_data and deleted_teams > 0:
                new_val = max(0, teams_joined - deleted_teams)
                cur.execute(
                    "UPDATE tournaments SET teams_joined = ? WHERE guild_id = ?",
                    (new_val, guild.id),
                )
                teams_joined = new_val

            conn.commit()
        except Exception as e:
            conn.rollback()
            log.exception("DB error in /bots clear: %s", e)
        finally:
            conn.close()

        # refresh panels if helpers exist
        t_cog = self.bot.get_cog("TournamentCog")
        if t_cog is not None:
            try:
                refresh_admin = getattr(t_cog, "refresh_admin_panels", None)
                if callable(refresh_admin):
                    await refresh_admin(guild)

                refresh_join = getattr(t_cog, "refresh_join_panels", None)
                if callable(refresh_join):
                    await refresh_join(guild)
            except Exception as e:
                log.exception("Error refreshing panels after /bots clear: %s", e)

        await interaction.followup.send(
            f"🧹 Cleared **{deleted_teams}** bot teams (channels + roles) "
            f"and removed stored bot players.\n"
            f"Updated `teams_joined` = `{teams_joined}`.",
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(BotsTestingCog(bot))
