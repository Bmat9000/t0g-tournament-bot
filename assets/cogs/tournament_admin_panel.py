# cogs/tournament_admin_panel.py
import logging
from typing import Dict, Any

import discord
from discord.ui import Modal, TextInput
from discord.ext import commands  # needed for setup()

from .tournament_db import get_tournament, upsert_tournament, delete_tournament
from .tournament_db import DB_PATH  # optional, mainly for logging if you want it

# We only import the refresh helper (no circular import, join_panel_cog does not import this file)
try:
    from .join_panel_cog import refresh_join_panel_message
except Exception:
    # In case join_panel_cog isn't loaded yet, we'll just skip refreshing the join panel
    async def refresh_join_panel_message(guild: discord.Guild):
        return

log = logging.getLogger(__name__)


# ---------- OPTIONAL PER-CHANNEL INITIALIZERS ----------

async def init_announcements_channel(channel: discord.TextChannel) -> None:
    try:
        from .tournament_announcements_cog import setup_tournament_announcements_channel
    except Exception:
        log.debug("No tournament_announcements_cog.setup_tournament_announcements_channel found; skipping.")
        return

    try:
        await setup_tournament_announcements_channel(channel)
    except Exception as e:
        log.exception("Error in setup_tournament_announcements_channel for %s: %r", channel.id, e)


async def init_rules_channel(channel: discord.TextChannel) -> None:
    try:
        from .tournament_rules_cog import setup_tournament_rules_channel
    except Exception:
        log.debug("No tournament_rules_cog.setup_tournament_rules_channel found; skipping.")
        return

    try:
        await setup_tournament_rules_channel(channel)
    except Exception as e:
        log.exception("Error in setup_tournament_rules_channel for %s: %r", channel.id, e)


async def init_create_team_channel(channel: discord.TextChannel) -> None:
    try:
        from .tournament_create_team_cog import setup_create_team_channel
    except Exception:
        log.debug("No tournament_create_team_cog.setup_create_team_channel found; skipping.")
        return

    try:
        await setup_create_team_channel(channel)
    except Exception as e:
        log.exception("Error in setup_create_team_channel for %s: %r", channel.id, e)


async def init_teams_channel(channel: discord.TextChannel) -> None:
    try:
        from .tournament_teams_cog import setup_tournament_teams_channel
    except Exception:
        log.debug("No tournament_teams_cog.setup_tournament_teams_channel found; skipping.")
        return

    try:
        await setup_tournament_teams_channel(channel)
    except Exception as e:
        log.exception("Error in setup_tournament_teams_channel for %s: %r", channel.id, e)


async def init_bracket_channel(channel: discord.TextChannel) -> None:
    try:
        from .tournament_bracket_cog import setup_bracket_and_scores_channel
    except Exception:
        log.debug("No tournament_bracket_cog.setup_bracket_and_scores_channel found; skipping.")
        return

    try:
        await setup_bracket_and_scores_channel(channel)
    except Exception as e:
        log.exception("Error in setup_bracket_and_scores_channel for %s: %r", channel.id, e)


async def init_results_channel(channel: discord.TextChannel) -> None:
    try:
        from .tournament_results_cog import setup_match_results_channel
    except Exception:
        log.debug("No tournament_results_cog.setup_match_results_channel found; skipping.")
        return

    try:
        await setup_match_results_channel(channel)
    except Exception as e:
        log.exception("Error in setup_match_results_channel for %s: %r", channel.id, e)


# ---------- Embed Builder (Admin Panel) ----------

def build_tournament_embed(t: Dict[str, Any]) -> discord.Embed:
    name = t["name"]
    max_teams = t["max_teams"]
    teams_joined = t.get("teams_joined", 0)
    team_size = t["team_size"]
    best_of = t["best_of"]
    bracket_type = t["bracket_type"]
    captain_scoring = bool(t["captain_scoring"])
    screenshot_proof = bool(t["screenshot_proof"])
    queue_status = t["queue_status"]
    status = t["status"]

    embed = discord.Embed(
        title="🛠️ TOURNAMENT CONTROL PANEL",
        description=f"Tournament Name: **{name}**",
        color=discord.Color.red()
    )

    embed.add_field(
        name="Teams Joined",
        value=f"{teams_joined} / {max_teams}\n"
              f"(Recommended bracket sizes: **4, 8, 16, 32**)",
        inline=False
    )

    embed.add_field(
        name="Team Size",
        value=f"{team_size} (1–6)",
        inline=True
    )

    embed.add_field(
        name="Match Format",
        value=f"Best-of-{best_of} Games\n(1 = BO1, 3 = BO3, 5 = BO5)",
        inline=True
    )

    embed.add_field(
        name="Bracket Type",
        value=bracket_type,
        inline=True
    )

    embed.add_field(
        name="Captain Scoring",
        value="ON (Captains + Admins)" if captain_scoring else "OFF (Admins Only)",
        inline=True
    )

    embed.add_field(
        name="Screenshot Proof",
        value="ON" if screenshot_proof else "OFF",
        inline=True
    )

    embed.add_field(
        name="Queue Status",
        value=queue_status,
        inline=True
    )

    embed.add_field(
        name="Tournament Status",
        value=status,
        inline=True
    )

    embed.set_footer(text="Use the tournament commands to manage your tournament.")
    return embed


# ---------- Control Panel Updater (no buttons) ----------

async def update_panel_message(guild: discord.Guild, t: Dict[str, Any]) -> None:
    channel_id = t.get("panel_channel_id")
    message_id = t.get("panel_message_id")
    if not channel_id or not message_id:
        return

    channel = guild.get_channel(channel_id)
    if not channel:
        return

    try:
        msg = await channel.fetch_message(message_id)
    except discord.NotFound:
        return

    embed = build_tournament_embed(t)
    await msg.edit(embed=embed)


# ---------- Modals ----------

class CreateTournamentModal(Modal, title="Create New Tournament"):
    def __init__(self, cog: "TournamentCog"):
        super().__init__(timeout=None)
        self.cog = cog

        self.name_input = TextInput(
            label="Tournament Name",
            placeholder="T0G 2v2 Weekend Cup",
            max_length=100
        )
        self.max_teams_input = TextInput(
            label="Max Teams",
            placeholder="8, 16, 32...",
            max_length=3
        )
        self.best_of_input = TextInput(
            label="Match Format (Best-of-X Games)",
            placeholder="1, 3, or 5",
            max_length=1
        )
        self.team_size_input = TextInput(
            label="Team Size (1–6)",
            placeholder="2 for 2v2, 3 for 3v3...",
            max_length=1
        )

        self.add_item(self.name_input)
        self.add_item(self.max_teams_input)
        self.add_item(self.best_of_input)
        self.add_item(self.team_size_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        from .tournament_db import upsert_tournament  # local import to avoid cycles

        guild = interaction.guild
        if not guild:
            await interaction.response.send_message(
                "❌ This command can only be used in a server.",
                ephemeral=True
            )
            return

        # Validate inputs
        name = self.name_input.value.strip()
        try:
            max_teams = int(self.max_teams_input.value.strip())
        except ValueError:
            await interaction.response.send_message(
                "❌ Max Teams must be a number.",
                ephemeral=True
            )
            return

        try:
            best_of = int(self.best_of_input.value.strip())
        except ValueError:
            await interaction.response.send_message(
                "❌ Best-of must be 1, 3, or 5.",
                ephemeral=True
            )
            return

        try:
            team_size = int(self.team_size_input.value.strip())
        except ValueError:
            await interaction.response.send_message(
                "❌ Team Size must be a number.",
                ephemeral=True
            )
            return

        if max_teams <= 0:
            await interaction.response.send_message(
                "❌ Max Teams must be greater than 0.",
                ephemeral=True
            )
            return

        if best_of not in (1, 3, 5):
            await interaction.response.send_message(
                "❌ Best-of must be **1**, **3**, or **5**.",
                ephemeral=True
            )
            return

        if not (1 <= team_size <= 6):
            await interaction.response.send_message(
                "❌ Team Size must be between **1** and **6**.",
                ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        # Create OR reuse roles for this tournament
        player_role_name = f"{name} Player"
        spectator_role_name = f"{name} Spectator"

        player_role = discord.utils.get(guild.roles, name=player_role_name)
        spectator_role = discord.utils.get(guild.roles, name=spectator_role_name)

        if player_role is None:
            player_role = await guild.create_role(
                name=player_role_name,
                mentionable=True,
                reason="Tournament player role for T0G Tournament Bot"
            )

        if spectator_role is None:
            spectator_role = await guild.create_role(
                name=spectator_role_name,
                mentionable=True,
                reason="Tournament spectator role for T0G Tournament Bot"
            )

        # Base category perms
        base_overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            guild.me: discord.PermissionOverwrite(
                view_channel=True,
                manage_channels=True,
                send_messages=True,
                read_message_history=True,
            ),
            player_role: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
            ),
            spectator_role: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=False,
                read_message_history=True,
            ),
        }

        category = await guild.create_category(
            f"🎮  {name}",
            overwrites=base_overwrites,
            reason="Tournament category created by T0G Tournament Bot"
        )

        # Admin channel (hidden from players/spectators)
        admin_overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            guild.me: base_overwrites[guild.me],
            player_role: discord.PermissionOverwrite(view_channel=False),
            spectator_role: discord.PermissionOverwrite(view_channel=False),
        }

        # Read-only channels
        read_only_player = discord.PermissionOverwrite(
            view_channel=True,
            send_messages=False,
            read_message_history=True,
        )
        read_only_spectator = discord.PermissionOverwrite(
            view_channel=True,
            send_messages=False,
            read_message_history=True,
        )

        announcements_overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            guild.me: base_overwrites[guild.me],
            player_role: read_only_player,
            spectator_role: read_only_spectator,
        }
        rules_overwrites = announcements_overwrites
        create_team_overwrites = announcements_overwrites
        teams_overwrites = announcements_overwrites
        bracket_overwrites = announcements_overwrites
        results_overwrites = announcements_overwrites

        # Tournament chat: players & spectators can talk
        chat_overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            guild.me: base_overwrites[guild.me],
            player_role: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
            ),
            spectator_role: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
            ),
        }

        # Create channels
        admin_channel = await guild.create_text_channel(
            "🔒│tournament-admin",
            category=category,
            overwrites=admin_overwrites
        )
        announcements_channel = await guild.create_text_channel(
            "📢│tournament-announcements",
            category=category,
            overwrites=announcements_overwrites
        )
        rules_channel = await guild.create_text_channel(
            "📜│tournament-rules",
            category=category,
            overwrites=rules_overwrites
        )
        create_team_channel = await guild.create_text_channel(
            "🏷│create-team",
            category=category,
            overwrites=create_team_overwrites
        )
        teams_channel = await guild.create_text_channel(
            "🧾│tournament-teams",
            category=category,
            overwrites=teams_overwrites
        )
        chat_channel = await guild.create_text_channel(
            "💬│tournament-chat",
            category=category,
            overwrites=chat_overwrites
        )
        bracket_channel = await guild.create_text_channel(
            "🏆│bracket-and-scores",
            category=category,
            overwrites=bracket_overwrites
        )
        results_channel = await guild.create_text_channel(
            "🎯│match-results",
            category=category,
            overwrites=results_overwrites
        )

        # Init per-channel content
        await init_announcements_channel(announcements_channel)
        await init_rules_channel(rules_channel)
        await init_create_team_channel(create_team_channel)
        await init_teams_channel(teams_channel)
        await init_bracket_channel(bracket_channel)
        await init_results_channel(results_channel)

        data = {
            "name": name,
            "max_teams": max_teams,
            "team_size": team_size,
            "best_of": best_of,
            "bracket_type": "Single Elim",
            "captain_scoring": 0,
            "screenshot_proof": 0,
            "queue_status": "CLOSED",
            "status": "WAITING",
            "panel_channel_id": admin_channel.id,
            "panel_message_id": None,
            "teams_joined": 0,
            "category_id": category.id,
            "player_role_id": player_role.id,
            "spectator_role_id": spectator_role.id,
            "players_joined": 0,
            "spectators_joined": 0,
            "join_panel_channel_id": None,
            "join_panel_message_id": None,
            "teams_channel_id": teams_channel.id,
        }

        upsert_tournament(guild.id, data)
        log.info("Guild %s: Created/updated tournament %r", guild.id, data["name"])

        embed = build_tournament_embed(data)
        panel_message = await admin_channel.send(embed=embed)

        data["panel_message_id"] = panel_message.id
        upsert_tournament(guild.id, data)

        await interaction.followup.send(
            f"✅ Tournament **{name}** created.\n"
            f"📁 Category: **{category.name}**\n"
            f"📺 Admin Panel: {admin_channel.mention}\n"
            f"🎭 Player Role: {player_role.mention}\n"
            f"👀 Spectator Role: {spectator_role.mention}",
            ephemeral=True
        )


class EditTournamentModal(Modal, title="Edit Tournament Settings"):
    def __init__(self, guild_id: int, existing: Dict[str, Any]):
        super().__init__(timeout=None)
        self.guild_id = guild_id
        self.existing = existing

        self.name_input = TextInput(
            label="Tournament Name",
            default=existing["name"],
            max_length=100
        )
        self.max_teams_input = TextInput(
            label="Max Teams",
            default=str(existing["max_teams"]),
            max_length=3
        )
        self.best_of_input = TextInput(
            label="Match Format (Best-of-X Games)",
            default=str(existing["best_of"]),
            max_length=1
        )
        self.team_size_input = TextInput(
            label="Team Size (1–6)",
            default=str(existing["team_size"]),
            max_length=1
        )

        self.add_item(self.name_input)
        self.add_item(self.max_teams_input)
        self.add_item(self.best_of_input)
        self.add_item(self.team_size_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        if not guild:
            await interaction.response.send_message(
                "❌ This command can only be used in a server.",
                ephemeral=True
            )
            return

        name = self.name_input.value.strip()

        try:
            max_teams = int(self.max_teams_input.value.strip())
            best_of = int(self.best_of_input.value.strip())
            team_size = int(self.team_size_input.value.strip())
        except ValueError:
            await interaction.response.send_message(
                "❌ Max Teams, Best-of, and Team Size must all be numbers.",
                ephemeral=True
            )
            return

        if max_teams <= 0:
            await interaction.response.send_message(
                "❌ Max Teams must be greater than 0.",
                ephemeral=True
            )
            return

        if best_of not in (1, 3, 5):
            await interaction.response.send_message(
                "❌ Best-of must be **1**, **3**, or **5**.",
                ephemeral=True
            )
            return

        if not (1 <= team_size <= 6):
            await interaction.response.send_message(
                "❌ Team Size must be between **1** and **6**.",
                ephemeral=True
            )
            return

        t = get_tournament(guild.id)
        if not t:
            await interaction.response.send_message(
                "❌ Tournament not found in storage.",
                ephemeral=True
            )
            return

        t["name"] = name
        t["max_teams"] = max_teams
        t["best_of"] = best_of
        t["team_size"] = team_size

        upsert_tournament(guild.id, t)
        log.info("Guild %s: Tournament settings edited by %s", guild.id, interaction.user)

        await update_panel_message(guild, t)
        await refresh_join_panel_message(guild)

        await interaction.response.send_message(
            "✅ Tournament settings updated.",
            ephemeral=True
        )


class DeleteTournamentModal(Modal, title="Confirm Tournament Deletion"):
    def __init__(self, guild_id: int):
        super().__init__(timeout=None)
        self.guild_id = guild_id

        self.confirm_input = TextInput(
            label="Type DELETE to confirm",
            placeholder="DELETE",
            max_length=10,
            required=True
        )

        self.add_item(self.confirm_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        # Check confirmation text
        if self.confirm_input.value.strip().upper() != "DELETE":
            await interaction.response.send_message(
                "❌ Confirmation failed. You must type **DELETE** exactly to delete the tournament.",
                ephemeral=True
            )
            return

        guild = interaction.guild
        if not guild:
            await interaction.response.send_message(
                "❌ This can only be used in a server.",
                ephemeral=True
            )
            return

        t = get_tournament(self.guild_id)
        if not t:
            await interaction.response.send_message(
                "❌ Tournament not found in storage.",
                ephemeral=True
            )
            return

        # Try to delete stored invite if present
        invite_code = t.get("join_invite_code")
        if invite_code:
            try:
                invite = await interaction.client.fetch_invite(invite_code)
                try:
                    await invite.delete(reason=f"Tournament deleted by {interaction.user}")
                    log.info(
                        "Guild %s: Deleted invite %s while deleting tournament",
                        guild.id,
                        invite_code,
                    )
                except Exception:
                    log.warning(
                        "Guild %s: Could not delete invite %s while deleting tournament",
                        guild.id,
                        invite_code,
                    )
            except Exception:
                log.warning(
                    "Guild %s: Could not fetch invite %s while deleting tournament",
                    guild.id,
                    invite_code,
                )

        category_id = t.get("category_id")
        player_role_id = t.get("player_role_id")
        spectator_role_id = t.get("spectator_role_id")

        category = guild.get_channel(category_id) if category_id else None
        player_role = guild.get_role(player_role_id) if player_role_id else None
        spectator_role = guild.get_role(spectator_role_id) if spectator_role_id else None

        channel_names = [
            "🔒│tournament-admin",
            "📢│tournament-announcements",
            "📜│tournament-rules",
            "🏷│create-team",
            "🧾│tournament-teams",
            "💬│tournament-chat",
            "🏆│bracket-and-scores",
            "🎯│match-results",
        ]

        await interaction.response.send_message(
            "🗑 Deleting tournament (categories, channels, team hubs, match channels, and roles)...",
            ephemeral=True
        )

        # 1) Main tournament category
        if isinstance(category, discord.CategoryChannel):
            for ch in list(category.channels):
                try:
                    await ch.delete(reason=f"Tournament deleted by {interaction.user}")
                except discord.Forbidden:
                    log.warning("Could not delete channel %s in guild %s", ch.id, guild.id)
            try:
                await category.delete(reason=f"Tournament deleted by {interaction.user}")
            except discord.Forbidden:
                log.warning("Could not delete category %s in guild %s", category.id, guild.id)

        # 2) Core channels by name (fallback)
        for name in channel_names:
            ch = discord.utils.get(guild.text_channels, name=name)
            if ch:
                try:
                    await ch.delete(reason=f"Tournament deleted by {interaction.user}")
                except discord.Forbidden:
                    log.warning("Could not delete channel %s in guild %s", ch.id, guild.id)

        # 3) ANY "Tournament Teams" category (team hubs)
        for cat in list(guild.categories):
            # normalize: lowercase + remove spaces so "Tournament   Teams", emojis, etc all match
            norm = "".join(cat.name.lower().split())
            if "tournamentteams" in norm:
                log.info("Deleting Tournament Teams category %r (%s)", cat.name, cat.id)
                for ch in list(cat.channels):
                    try:
                        await ch.delete(reason=f"Tournament deleted by {interaction.user}")
                    except discord.Forbidden:
                        log.warning("Could not delete team channel %s in guild %s", ch.id, guild.id)
                try:
                    await cat.delete(reason=f"Tournament deleted by {interaction.user}")
                except discord.Forbidden:
                    log.warning("Could not delete teams category %s in guild %s", cat.id, guild.id)

        # 4) ANY matches category for this tournament (match channels)
        tourney_name_norm = (t.get("name") or "").lower().replace(" ", "")
        for cat in list(guild.categories):
            norm = "".join(cat.name.lower().split())
            # true if this cat name includes the tourney name + "matches"
            is_named_for_this_tourney = (
                tourney_name_norm
                and tourney_name_norm in norm
                and "matches" in norm
            )
            # legacy / generic detection (old naming)
            legacy_match = (
                "tournamentmatches" in norm
                or ("tournament" in norm and "match" in norm)
            )
            if is_named_for_this_tourney or legacy_match:
                log.info("Deleting Tournament Matches category %r (%s)", cat.name, cat.id)
                for ch in list(cat.channels):
                    try:
                        await ch.delete(reason=f"Tournament deleted by {interaction.user}")
                    except discord.Forbidden:
                        log.warning("Could not delete match channel %s in guild %s", ch.id, guild.id)
                try:
                    await cat.delete(reason=f"Tournament deleted by {interaction.user}")
                except discord.Forbidden:
                    log.warning("Could not delete matches category %s in guild %s", cat.id, guild.id)

        # 5) Any leftover team-* channels (team hubs)
        for ch in list(guild.text_channels):
            # Old naming (no emoji): "team-xyz"
            # New naming (with emoji): "🛡│team-xyz"
            if ch.name.startswith("team-") or "team-" in ch.name:
                try:
                    await ch.delete(reason=f"Tournament deleted by {interaction.user}")
                except discord.Forbidden:
                    log.warning("Could not delete team channel %s in guild %s", ch.id, guild.id)

        # 6) Roles (player + spectator + bot teams + human teams)
        roles_to_delete = set()

        # Player role (from DB or fallback by name)
        if player_role:
            roles_to_delete.add(player_role)
        else:
            tourney_name = t.get("name")
            if tourney_name:
                r = discord.utils.get(guild.roles, name=f"{tourney_name} Player")
                if r:
                    roles_to_delete.add(r)

        # Spectator role
        if spectator_role:
            roles_to_delete.add(spectator_role)

        # Extra cleanup for: Bot Team roles + human Team | roles
        for role in guild.roles:
            # Bot-created test teams
            if role.name.startswith("Bot Team "):
                roles_to_delete.add(role)

            # Human-created teams from /create-team (e.g. "Team | T0G Demons")
            if role.name.startswith("Team | "):
                roles_to_delete.add(role)

        for role in roles_to_delete:
            try:
                await role.delete(reason=f"Tournament deleted by {interaction.user}")
            except discord.Forbidden:
                log.warning("Could not delete role %s in guild %s", role.id, guild.id)

        delete_tournament(guild.id)
        log.info("Guild %s: Tournament deleted by %s", guild.id, interaction.user)


# ---------- REQUIRED FOR AUTO-LOADER ----------

async def setup(bot: commands.Bot):
    # This module now provides the admin panel helpers & modals (no buttons / no Cog).
    log.info("tournament_admin_panel module loaded (panel helpers & modals only).")
