# cogs/join_panel_cog.py
import logging
import sqlite3
from typing import Optional, Dict, Any

import discord
from discord import app_commands
from discord.ext import commands
from discord.ui import View, button

log = logging.getLogger(__name__)

# Centralized paths / .env loader
from core.config import DB_PATH


# ---------- Small DB Helpers ----------

def get_tournament(guild_id: int) -> Optional[Dict[str, Any]]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM tournaments WHERE guild_id = ?", (guild_id,))
    row = cur.fetchone()
    conn.close()
    if not row:
        return None
    return dict(row)


def set_join_panel_message(
    guild_id: int,
    channel_id: int,
    message_id: int,
    invite_code: Optional[str] = None,
) -> None:
    """
    Save where the join panel lives + (optionally) the invite code used for that channel.
    Requires a 'join_invite_code' column in the tournaments table.
    """
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    if invite_code is not None:
        cur.execute(
            """
            UPDATE tournaments
            SET join_panel_channel_id = ?, join_panel_message_id = ?, join_invite_code = ?
            WHERE guild_id = ?
            """,
            (channel_id, message_id, invite_code, guild_id),
        )
    else:
        cur.execute(
            """
            UPDATE tournaments
            SET join_panel_channel_id = ?, join_panel_message_id = ?
            WHERE guild_id = ?
            """,
            (channel_id, message_id, guild_id),
        )

    conn.commit()
    conn.close()


def adjust_counts(guild_id: int, player_delta: int = 0, spectator_delta: int = 0) -> None:
    """Adjust cached player/spectator counts for the tournament in this guild."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row  # <-- make rows dict-like
    cur = conn.cursor()

    cur.execute(
        "SELECT players_joined, spectators_joined FROM tournaments WHERE guild_id = ?",
        (guild_id,),
    )
    row = cur.fetchone()
    if not row:
        conn.close()
        return

    players = max(0, row["players_joined"] + player_delta)
    spectators = max(0, row["spectators_joined"] + spectator_delta)

    cur.execute(
        "UPDATE tournaments SET players_joined = ?, spectators_joined = ? WHERE guild_id = ?",
        (players, spectators, guild_id),
    )
    conn.commit()
    conn.close()


# ---------- Join Panel Embed Builder + Refresher ----------

def build_join_embed(guild: discord.Guild, t: Dict[str, Any]) -> discord.Embed:
    name = t["name"]
    max_teams = t["max_teams"]
    teams_joined = t.get("teams_joined", 0)
    players_joined = t.get("players_joined", 0)
    spectators_joined = t.get("spectators_joined", 0)
    team_size = t["team_size"]
    queue_status = t["queue_status"]

    # Find channels by their fixed names
    create_team_ch = discord.utils.get(guild.text_channels, name="🏷│create-team")
    chat_ch = discord.utils.get(guild.text_channels, name="💬│tournament-chat")
    rules_ch = discord.utils.get(guild.text_channels, name="📜│tournament-rules")

    ct_mention = create_team_ch.mention if create_team_ch else "`#create-team`"
    chat_mention = chat_ch.mention if chat_ch else "`#tournament-chat`"
    rules_mention = rules_ch.mention if rules_ch else "`#tournament-rules`"

    status_text = (
        "🟢 **OPEN** – Players can join."
        if queue_status == "OPEN"
        else "🔴 **CLOSED** – Players cannot join."
    )

    # Optional invite link (created when /tournament_join_panel is used)
    invite_code = t.get("join_invite_code")
    invite_line = ""
    if invite_code:
        invite_line = f"🔗 Share this invite to join: https://discord.gg/{invite_code}\n\n"

    desc = (
        f"Tournament: **{name}**\n"
        f"Status: {status_text}\n"
        f"Teams: **{teams_joined} / {max_teams}** | Team Size: **{team_size}**\n"
        f"Players: **{players_joined}** | Spectators: **{spectators_joined}**\n\n"
        f"{invite_line}"
        f"🧾 Create your team in {ct_mention}\n"
        f"💬 Use {chat_mention} for all tournament chat\n"
        f"📜 Don't forget to read the rules in {rules_mention}\n\n"
        f"👀 **Spectators** can see everything except the admin channel and can only type in {chat_mention}.\n"
        f"🚪 If you ever want to leave, click **Leave Tournament** below."
    )

    embed = discord.Embed(
        title="🎮 Join Tournament",
        description=desc,
        color=discord.Color.green(),
    )
    return embed


async def refresh_join_panel_message(guild: discord.Guild) -> None:
    """
    Used by BOTH this cog and the tournament admin panel to keep the
    join panel embed in sync (status, counts, etc).
    """
    if guild is None:
        return

    t = get_tournament(guild.id)
    if not t:
        return

    channel_id = t.get("join_panel_channel_id")
    message_id = t.get("join_panel_message_id")
    if not channel_id or not message_id:
        return

    channel = guild.get_channel(channel_id)
    if not isinstance(channel, discord.TextChannel):
        return

    try:
        msg = await channel.fetch_message(message_id)
    except discord.NotFound:
        return

    embed = build_join_embed(guild, t)
    view = JoinTournamentView()
    await msg.edit(embed=embed, view=view)


# ---------- Join Panel View ----------

class JoinTournamentView(View):
    """
    View attached to the public 'Join Tournament' message.
    - Join as Player
    - Spectate Only
    - Leave Tournament
    """
    def __init__(self):
        # timeout=None + custom_id on each button => persistent view
        super().__init__(timeout=None)

    @button(
        label="✅ Join as Player",
        style=discord.ButtonStyle.success,
        custom_id="t0g_join_tournament_player",
    )
    async def join_player(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        if not guild:
            await interaction.response.send_message(
                "❌ This button only works inside a server.",
                ephemeral=True,
            )
            return

        t = get_tournament(guild.id)
        if not t:
            await interaction.response.send_message(
                "❌ No active tournament found. The host may have ended it.",
                ephemeral=True,
            )
            return

        # Respect queue status: players cannot join when CLOSED
        if t["queue_status"] != "OPEN":
            await interaction.response.send_message(
                "🔴 Player join is currently **CLOSED**. Wait for the host to open joins again.",
                ephemeral=True,
            )
            return

        player_role = guild.get_role(t.get("player_role_id") or 0)
        spectator_role = guild.get_role(t.get("spectator_role_id") or 0)
        if not player_role:
            await interaction.response.send_message(
                "⚠ Player role for this tournament is missing. Ask the host to recreate the tournament.",
                ephemeral=True,
            )
            return

        member = interaction.user

        # Already a player?
        if player_role in member.roles:
            await interaction.response.send_message(
                "✅ You’re already joined as a **Player** in this tournament.",
                ephemeral=True,
            )
            return

        # Capacity check (max players = max_teams * team_size)
        max_players = t["max_teams"] * t["team_size"]
        current_players = t.get("players_joined", 0)
        if current_players >= max_players:
            await interaction.response.send_message(
                "⚠ Player spots are currently **full**. You can still join as a **Spectator**.",
                ephemeral=True,
            )
            return

        # Track whether they were a spectator BEFORE we touch roles
        had_spectator = bool(spectator_role and (spectator_role in member.roles))

        # Apply roles: remove spectator if they had it, then add player
        roles_to_add = []
        roles_to_remove = []
        if had_spectator:
            roles_to_remove.append(spectator_role)
        roles_to_add.append(player_role)

        try:
            if roles_to_remove:
                await member.remove_roles(*roles_to_remove, reason="Switching to Tournament Player")
            await member.add_roles(*roles_to_add, reason="Joined tournament as Player")
        except discord.Forbidden:
            await interaction.response.send_message(
                "⚠ I don't have permission to manage your roles.",
                ephemeral=True,
            )
            return

        # Update counts: +1 player, -1 spectator if they had spectator before
        adjust_counts(
            guild.id,
            player_delta=1,
            spectator_delta=(-1 if had_spectator else 0),
        )

        # Refresh embed (players count / status)
        await refresh_join_panel_message(guild)

        # Mention channels
        create_team_ch = discord.utils.get(guild.text_channels, name="🏷│create-team")
        chat_ch = discord.utils.get(guild.text_channels, name="💬│tournament-chat")
        rules_ch = discord.utils.get(guild.text_channels, name="📜│tournament-rules")

        ct_mention = create_team_ch.mention if create_team_ch else "`#create-team`"
        chat_mention = chat_ch.mention if chat_ch else "`#tournament-chat`"
        rules_mention = rules_ch.mention if rules_ch else "`#tournament-rules`"

        await interaction.response.send_message(
            f"✅ You are now registered as a **Player** for **{t['name']}**.\n\n"
            f"🧾 Go to {ct_mention} to **create your team** and invite teammates.\n"
            f"For solo tournaments, you still create a team – you just won't be able to add anyone else.\n\n"
            f"💬 Use {chat_mention} for all tournament chat.\n"
            f"📜 And don't forget to read the rules in {rules_mention}.",
            ephemeral=True,
        )

    @button(
        label="👀 Spectate Only",
        style=discord.ButtonStyle.primary,
        custom_id="t0g_join_tournament_spectator",
    )
    async def join_spectator(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        if not guild:
            await interaction.response.send_message(
                "❌ This button only works inside a server.",
                ephemeral=True,
            )
            return

        t = get_tournament(guild.id)
        if not t:
            await interaction.response.send_message(
                "❌ No active tournament found. The host may have ended it.",
                ephemeral=True,
            )
            return

        # Spectators are allowed even if queue is CLOSED
        player_role = guild.get_role(t.get("player_role_id") or 0)
        spectator_role = guild.get_role(t.get("spectator_role_id") or 0)
        if not spectator_role:
            await interaction.response.send_message(
                "⚠ Spectator role for this tournament is missing. Ask the host to recreate the tournament.",
                ephemeral=True,
            )
            return

        member = interaction.user

        # Already a player?
        if player_role and player_role in member.roles:
            await interaction.response.send_message(
                "⚠ You’re currently a **Player** in this tournament.\n"
                "If you want to spectate instead, leave the tournament first and then choose **Spectate Only**.",
                ephemeral=True,
            )
            return

        # Already a spectator?
        if spectator_role in member.roles:
            await interaction.response.send_message(
                "✅ You’re already a **Spectator** for this tournament.",
                ephemeral=True,
            )
            return

        try:
            await member.add_roles(spectator_role, reason="Joined tournament as Spectator")
        except discord.Forbidden:
            await interaction.response.send_message(
                "⚠ I don't have permission to manage your roles.",
                ephemeral=True,
            )
            return

        # Update counts
        adjust_counts(guild.id, player_delta=0, spectator_delta=1)

        # Refresh embed
        await refresh_join_panel_message(guild)

        chat_ch = discord.utils.get(guild.text_channels, name="💬│tournament-chat")
        rules_ch = discord.utils.get(guild.text_channels, name="📜│tournament-rules")

        chat_mention = chat_ch.mention if chat_ch else "`#tournament-chat`"
        rules_mention = rules_ch.mention if rules_ch else "`#tournament-rules`"

        await interaction.response.send_message(
            f"👀 You are now a **Spectator** for **{t['name']}**.\n\n"
            f"You can view the tournament and chat in {chat_mention}.\n\n"
            f"If you don't want to spectate anymore, click **Leave Tournament** below.",
            ephemeral=True,
        )

    @button(
        label="🚪 Leave Tournament",
        style=discord.ButtonStyle.danger,
        custom_id="t0g_leave_tournament",
    )
    async def leave_tournament(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        if not guild:
            await interaction.response.send_message(
                "❌ This button only works inside a server.",
                ephemeral=True,
            )
            return

        t = get_tournament(guild.id)
        if not t:
            await interaction.response.send_message(
                "❌ No active tournament found.",
                ephemeral=True,
            )
            return

        player_role = guild.get_role(t.get("player_role_id") or 0)
        spectator_role = guild.get_role(t.get("spectator_role_id") or 0)

        member = interaction.user

        had_player = player_role in member.roles if player_role else False
        had_spectator = spectator_role in member.roles if spectator_role else False

        if not had_player and not had_spectator:
            await interaction.response.send_message(
                "ℹ You are not currently joined in this tournament as a player or spectator.",
                ephemeral=True,
            )
            return

        roles_to_remove = []
        if had_player:
            roles_to_remove.append(player_role)
        if had_spectator:
            roles_to_remove.append(spectator_role)

        try:
            if roles_to_remove:
                await member.remove_roles(*roles_to_remove, reason="Left tournament")
        except discord.Forbidden:
            await interaction.response.send_message(
                "⚠ I don't have permission to manage your roles.",
                ephemeral=True,
            )
            return

        # Update counts
        adjust_counts(
            guild.id,
            player_delta=(-1 if had_player else 0),
            spectator_delta=(-1 if had_spectator else 0),
        )

        # Refresh embed
        await refresh_join_panel_message(guild)

        await interaction.response.send_message(
            f"🚪 You have **left** the tournament **{t['name']}**.\n"
            "You can always re-join later using the buttons again.",
            ephemeral=True,
        )


# ---------- Cog ----------

class JoinPanelCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        log.info("JoinPanelCog loaded.")

    async def cog_load(self):
        # Persistent view so the buttons keep working after bot restarts
        self.bot.add_view(JoinTournamentView())

    # Helper: make sure command is only used in the admin panel channel
    def _ensure_admin_channel(self, interaction: discord.Interaction) -> Optional[str]:
        guild = interaction.guild
        if not guild:
            return "❌ This command can only be used in a server."

        t = get_tournament(guild.id)
        if not t:
            return "❌ No tournament found. Create one first with `/create_tournament`."

        panel_channel_id = t.get("panel_channel_id")
        if not panel_channel_id:
            return "❌ Admin panel channel is not set for this tournament."

        if interaction.channel_id != panel_channel_id:
            admin_channel = guild.get_channel(panel_channel_id)
            where = admin_channel.mention if admin_channel else "#tournament-admin"
            return f"❌ You can only use this command in {where}."
        return None

    @app_commands.command(
        name="tournament_join_panel",
        description="Post the Join Tournament panel in a channel (Manage Server only).",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def tournament_join_panel(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
    ):
        # 🔒 Only from the admin panel channel
        err = self._ensure_admin_channel(interaction)
        if err:
            await interaction.response.send_message(err, ephemeral=True)
            return

        guild = interaction.guild
        if not guild:
            await interaction.response.send_message(
                "❌ This command can only be used in a server.",
                ephemeral=True,
            )
            return

        t = get_tournament(guild.id)
        if not t:
            await interaction.response.send_message(
                "❌ No tournament found. Create one first with `/create_tournament`.",
                ephemeral=True,
            )
            return

        # Create (or refresh) a permanent invite for this join panel channel
        invite = await channel.create_invite(
            max_age=0,
            max_uses=0,
            unique=True,
            reason="Tournament join invite created by T0G Tournament Bot",
        )

        # Save invite code into tournament dict so the embed can show it
        t["join_invite_code"] = invite.code

        embed = build_join_embed(guild, t)
        view = JoinTournamentView()
        msg = await channel.send(embed=embed, view=view)

        # Save where the join panel lives (for live updates) + invite code
        set_join_panel_message(guild.id, channel.id, msg.id, invite.code)

        await interaction.response.send_message(
            f"✅ Join panel posted in {channel.mention}.",
            ephemeral=True,
        )

    @tournament_join_panel.error
    async def tournament_join_panel_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ):
        if isinstance(error, app_commands.errors.MissingPermissions):
            await interaction.response.send_message(
                "❌ You need **Manage Server** permission to use this command.",
                ephemeral=True,
            )
        else:
            log.exception("Error in /tournament_join_panel: %r", error)
            if interaction.response.is_done():
                await interaction.followup.send("❌ Something went wrong.", ephemeral=True)
            else:
                await interaction.response.send_message("❌ Something went wrong.", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(JoinPanelCog(bot))
