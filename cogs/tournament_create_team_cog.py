# cogs/tournament_create_team_cog.py
import asyncio
import logging
import re
from typing import Optional, List

import discord
from discord.ext import commands
from discord.ui import View, Modal, TextInput

from .tournament_db import (
    get_tournament,
    upsert_tournament,
    add_team,
    set_team_ready,
    delete_team as db_delete_team,
)

log = logging.getLogger(__name__)

# Try to import the join-panel refresher (safe if it doesn't exist yet)
try:
    from .join_panel_cog import refresh_join_panel_message
except Exception:  # pragma: no cover - optional cog
    async def refresh_join_panel_message(guild: discord.Guild):
        return


# ------------- HELPERS -------------


def get_player_role(guild: discord.Guild, t: Optional[dict]) -> Optional[discord.Role]:
    """
    Try to find the tournament player role.
    Pattern (from you): if tourney name is 1111, role is '1111 Player'.
    We also try a stored player_role_id if present in the DB.
    """
    if t:
        # If DB stores a player_role_id or player_role key, try that first
        stored_id = t.get("player_role_id") or t.get("player_role")
        if stored_id:
            try:
                r = guild.get_role(int(stored_id))
                if r:
                    return r
            except (TypeError, ValueError):
                pass

        # If DB has a tournament name, try "<name> Player"
        name = t.get("name")
        if name:
            r = discord.utils.get(guild.roles, name=f"{name} Player")
            if r:
                return r

    # Fallback: first role that ends with " Player"
    for r in guild.roles:
        if r.name.endswith(" Player"):
            return r

    return None


# ------------- VIEW ON #create-team MESSAGE -------------


class CreateTeamView(View):
    """View that sits on the #create-team message and opens the team creation modal."""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="➕ Create Team",
        style=discord.ButtonStyle.primary,
        custom_id="t0g_create_team_button",
    )
    async def create_team(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        if not guild:
            await interaction.response.send_message(
                "❌ This can only be used in a server.",
                ephemeral=True,
            )
            return

        t = get_tournament(guild.id)
        if not t:
            await interaction.response.send_message(
                "❌ No active tournament found in this server.",
                ephemeral=True,
            )
            return

        # Open modal so the user can type their team name
        modal = CreateTeamModal()
        await interaction.response.send_modal(modal)


# ------------- MODAL: CREATE TEAM -------------


class CreateTeamModal(Modal, title="Create Your Team"):
    team_name: TextInput = TextInput(
        label="Team Name",
        placeholder="Example: T0G Demons",
        max_length=32,
    )

    async def on_submit(self, interaction: discord.Interaction):
        guild = interaction.guild
        user = interaction.user

        if not guild:
            await interaction.response.send_message(
                "❌ This can only be used in a server.",
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

        # Already in a team? Block creating another
        existing_team_role = discord.utils.find(
            lambda r: r.name.startswith("Team | "), user.roles
        )
        if existing_team_role:
            await interaction.response.send_message(
                f"❌ You are already in **{existing_team_role.name}**.\n"
                "You must leave or delete that team before creating a new one.",
                ephemeral=True,
            )
            return

        raw_name = str(self.team_name.value).strip()
        if len(raw_name) < 2:
            await interaction.response.send_message(
                "❌ Team name must be at least **2** characters.",
                ephemeral=True,
            )
            return

        # Simple uniqueness check by role name
        role_name = f"Team | {raw_name}"
        existing_role = discord.utils.get(guild.roles, name=role_name)
        if existing_role:
            await interaction.response.send_message(
                "❌ A team with that name already exists. Please choose another name.",
                ephemeral=True,
            )
            return

        # Defer because we're about to do multiple API calls
        await interaction.response.defer(ephemeral=True, thinking=True)

        # ---------- Create team role ----------
        team_role = await guild.create_role(
            name=role_name,
            mentionable=True,
            reason=f"Tournament team role created for {user} ({raw_name})",
        )

        # Give role to creator (captain)
        try:
            await user.add_roles(team_role, reason="Tournament team creator / captain")
        except discord.Forbidden:
            log.warning(
                "Could not add team role %s to %s in guild %s",
                team_role.id,
                user,
                guild.id,
            )

        # ---------- Find or create shared 'Tournament Teams' category ----------
        teams_category: Optional[discord.CategoryChannel] = None

        # Try to find an existing category that looks like the teams category
        for cat in guild.categories:
            name_lower = cat.name.lower()
            if "tournament" in name_lower and "team" in name_lower:
                teams_category = cat
                break

        # If none found, create it (and try to position it near the main tournament category)
        if teams_category is None:
            base_category: Optional[discord.CategoryChannel] = None
            category_id = t.get("category_id")
            if category_id:
                ch = guild.get_channel(category_id)
                if isinstance(ch, discord.CategoryChannel):
                    base_category = ch

            teams_category = await guild.create_category(
                name="🛡 Tournament Teams",
                reason="Tournament team hubs category",
            )

            if base_category is not None:
                try:
                    await teams_category.move(after=base_category)
                except Exception:
                    pass

        category: Optional[discord.CategoryChannel] = teams_category

        # ---------- Create team hub channel ----------
        slug = re.sub(r"[^a-z0-9]+", "-", raw_name.lower()).strip("-")
        if not slug:
            slug = "team"
        chan_name = f"🛡│team-{slug[:20]}"

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            guild.me: discord.PermissionOverwrite(
                view_channel=True,
                manage_channels=True,
                send_messages=True,
                read_message_history=True,
            ),
            team_role: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
            ),
        }

        team_channel = await guild.create_text_channel(
            chan_name,
            category=category,
            overwrites=overwrites,
            reason=f"Tournament team hub for {raw_name}",
        )

        # ---------- Update DB (teams_joined on tournaments table) ----------
        current = t.get("teams_joined") or 0
        try:
            current = int(current)
        except (TypeError, ValueError):
            current = 0

        t["teams_joined"] = current + 1
        upsert_tournament(guild.id, t)

        # ---------- Insert into teams table for bracket system ----------
        try:
            # Store the visible role name as the team_name so everything matches
            # (e.g. "Team | 11111")
            add_team(guild.id, team_role.name, team_role.id, user.id)
        except Exception:
            log.exception("Failed to insert human team into teams table.")

        # Try to refresh the admin panel embed
        try:
            from .tournament_admin_panel import update_panel_message as _update_panel
        except Exception:
            _update_panel = None

        if _update_panel is not None:
            try:
                await _update_panel(guild, t)
            except Exception:
                log.exception("Failed to update admin panel after team creation.")

        # Refresh join panel so it can show updated team count
        try:
            await refresh_join_panel_message(guild)
        except Exception:
            log.exception("Failed to refresh join panel after team creation.")

        # ---------- Send Team Hub message ----------
        hub_view = TeamHubView(team_role.id)
        hub_embed = discord.Embed(
            title=f"Team Hub — {raw_name}",
            description=(
                f"Welcome {team_role.mention}! This is your private team hub.\n\n"
                "Use the buttons below:\n"
                "• **Invites** – open a dropdown of tournament players to invite\n"
                "• **Ready** – mark your team as ready (posts to tournament teams list)\n"
                "• **Match Info** – view info once matches are generated\n"
                "• **Delete Team** – remove this team (channel, role, and listings)\n"
            ),
            color=discord.Color.blurple(),
        )
        await team_channel.send(embed=hub_embed, view=hub_view)

        # Final confirmation back to the user
        await interaction.followup.send(
            f"✅ Team **{raw_name}** created!\n"
            f"• Role: {team_role.mention}\n"
            f"• Team Channel: {team_channel.mention}\n"
            f"• Total Teams in Tournament: **{t['teams_joined']}**",
            ephemeral=True,
        )


# ------------- INVITE FLOW (DROPDOWN + DM ACCEPT/DECLINE) -------------


class InviteResponseView(View):
    """View in the DM for Accept / Decline of a team invite."""

    def __init__(self, guild_id: int, team_role_id: int, inviter_id: int):
        super().__init__(timeout=600)
        self.guild_id = guild_id
        self.team_role_id = team_role_id
        self.inviter_id = inviter_id

    @discord.ui.button(
        label="✅ Accept",
        style=discord.ButtonStyle.success,
        custom_id="t0g_invite_accept",
    )
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        user = interaction.user
        guild = interaction.client.get_guild(self.guild_id)
        if not guild:
            await interaction.response.send_message(
                "❌ I can't find the server this invite belongs to.",
                ephemeral=True,
            )
            return

        member = guild.get_member(user.id)
        if not member:
            await interaction.response.send_message(
                "❌ You are no longer in that server.",
                ephemeral=True,
            )
            return

        team_role = guild.get_role(self.team_role_id)
        if not team_role:
            await interaction.response.send_message(
                "❌ The team role no longer exists.",
                ephemeral=True,
            )
            return

        # Block if already in another team
        other_team = discord.utils.find(
            lambda r: r.name.startswith("Team | "), member.roles
        )
        if other_team and other_team != team_role:
            await interaction.response.send_message(
                f"❌ You are already in **{other_team.name}**.\n"
                "Leave that team before joining another.",
                ephemeral=True,
            )
            return

        try:
            await member.add_roles(team_role, reason="Accepted tournament team invite")
        except discord.Forbidden:
            await interaction.response.send_message(
                "❌ I don't have permission to give you that role.",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            f"✅ You have joined **{team_role.name}**.",
            ephemeral=True,
        )

        inviter = guild.get_member(self.inviter_id)
        if inviter:
            try:
                await inviter.send(
                    f"✅ {member.mention} accepted your team invite to **{team_role.name}**."
                )
            except Exception:
                pass

    @discord.ui.button(
        label="❌ Decline",
        style=discord.ButtonStyle.danger,
        custom_id="t0g_invite_decline",
    )
    async def decline(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            "❌ You declined the team invite.",
            ephemeral=True,
        )


class InviteSelect(discord.ui.Select):
    """Dropdown of tournament players to invite."""

    def __init__(self, guild_id: int, team_role_id: int, members: List[discord.Member]):
        self.guild_id = guild_id
        self.team_role_id = team_role_id

        options = [
            discord.SelectOption(label=m.display_name[:25], value=str(m.id))
            for m in members[:25]  # Discord max 25 options
        ]

        super().__init__(
            placeholder="Select a player to invite...",
            min_values=1,
            max_values=1,
            options=options,
            custom_id="t0g_invite_select",
        )

    async def callback(self, interaction: discord.Interaction):
        guild = interaction.client.get_guild(self.guild_id)
        if not guild:
            await interaction.response.send_message(
                "❌ I can't find the server this invite belongs to.",
                ephemeral=True,
            )
            return

        try:
            target_id = int(self.values[0])
        except (TypeError, ValueError):
            await interaction.response.send_message(
                "❌ Invalid selection.",
                ephemeral=True,
            )
            return

        target = guild.get_member(target_id)
        if not target:
            await interaction.response.send_message(
                "❌ That player is no longer in the server.",
                ephemeral=True,
            )
            return

        team_role = guild.get_role(self.team_role_id)
        if not team_role:
            await interaction.response.send_message(
                "❌ The team role no longer exists.",
                ephemeral=True,
            )
            return

        # Already in this team?
        if team_role in target.roles:
            await interaction.response.send_message(
                f"ℹ️ {target.mention} is already in **{team_role.name}**.",
                ephemeral=True,
            )
            return

        # Already in another team?
        other_team = discord.utils.find(
            lambda r: r.name.startswith("Team | "), target.roles
        )
        if other_team and other_team != team_role:
            await interaction.response.send_message(
                f"❌ {target.mention} is already in **{other_team.name}**.",
                ephemeral=True,
            )
            return

        dm_view = InviteResponseView(
            guild_id=self.guild_id,
            team_role_id=self.team_role_id,
            inviter_id=interaction.user.id,
        )

        try:
            await target.send(
                embed=discord.Embed(
                    title="🎮 Tournament Team Invite",
                    description=(
                        f"You have been invited to join **{team_role.name}** "
                        f"in **{guild.name}** by {interaction.user.mention}.\n\n"
                        "Use the buttons below to accept or decline."
                    ),
                    color=discord.Color.blurple(),
                ),
                view=dm_view,
            )
        except Exception:
            await interaction.response.send_message(
                f"❌ I couldn't DM {target.mention}. They might have DMs disabled.",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            f"✅ Invite sent to {target.mention}.",
            ephemeral=True,
        )


class InviteSelectView(View):
    """View wrapper for the InviteSelect."""

    def __init__(self, guild_id: int, team_role_id: int, members: List[discord.Member]):
        super().__init__(timeout=60)
        self.add_item(InviteSelect(guild_id, team_role_id, members))


# ------------- TEAM HUB VIEW (INVITES / READY / MATCH INFO / DELETE) -------------


class TeamHubView(View):
    """Buttons that live in the team hub channel."""

    def __init__(self, team_role_id: int):
        super().__init__(timeout=None)
        self.team_role_id = team_role_id
        self.ready = False

    @discord.ui.button(
        label="👥 Invites",
        style=discord.ButtonStyle.secondary,
        custom_id="t0g_team_invites",
    )
    async def invites(self, interaction: discord.Interaction, button: discord.ui.Button):
        """
        Open a dropdown of tournament players to invite.
        Players are those with the '<tournament name> Player' role and not already in this team.
        """
        guild = interaction.guild
        if not guild:
            await interaction.response.send_message(
                "❌ This can only be used in a server.",
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

        player_role = get_player_role(guild, t)
        if not player_role:
            await interaction.response.send_message(
                "❌ I couldn't find the tournament player role.",
                ephemeral=True,
            )
            return

        team_role = guild.get_role(self.team_role_id)
        if not team_role:
            await interaction.response.send_message(
                "❌ The team role for this hub no longer exists.",
                ephemeral=True,
            )
            return

        # Only allow team members to send invites
        if team_role not in interaction.user.roles:
            await interaction.response.send_message(
                "❌ Only team members can send invites for this team.",
                ephemeral=True,
            )
            return

        candidates: List[discord.Member] = []
        for m in guild.members:
            if player_role in m.roles and team_role not in m.roles:
                candidates.append(m)

        if not candidates:
            join_channel = None
            join_channel_id = t.get("join_panel_channel_id")
            if join_channel_id:
                ch = guild.get_channel(join_channel_id)
                if isinstance(ch, discord.TextChannel):
                    join_channel = ch

            invite_code = t.get("join_invite_code")
            invite_link = f"https://discord.gg/{invite_code}" if invite_code else None

            lines = [
                "ℹ️ There are no eligible tournament players to invite right now.",
                "",
            ]

            if join_channel:
                lines.append(
                    f"• Ask your teammate to **join the tournament** in {join_channel.mention} first."
                )
            else:
                lines.append(
                    "• Ask your teammate to **join the tournament** using the tournament join panel first."
                )

            if invite_link:
                lines.append(
                    f"• If they are not in the server yet, send them this invite link: {invite_link}"
                )
            else:
                lines.append(
                    "• If they are not in the server yet, invite them to the server (ask an admin if you need help)."
                )

            await interaction.response.send_message(
                "\n".join(lines),
                ephemeral=True,
            )
            return

        view = InviteSelectView(guild.id, self.team_role_id, candidates)
        await interaction.response.send_message(
            "Select a player to invite to your team:",
            view=view,
            ephemeral=True,
        )

    @discord.ui.button(
        label="✅ Ready Up",
        style=discord.ButtonStyle.success,
        custom_id="t0g_team_ready",
    )
    async def ready_up(self, interaction: discord.Interaction, button: discord.ui.Button):
        """
        Toggle ready state and post a roster embed in #tournament-teams.

        - Checks tournament team_size (1–6).
        - If team is NOT full, it will NOT let them ready up.
        """
        guild = interaction.guild
        if not guild:
            await interaction.response.send_message(
                "❌ This can only be used in a server.",
                ephemeral=True,
            )
            return

        team_role = guild.get_role(self.team_role_id)
        if not team_role:
            await interaction.response.send_message(
                "❌ The team role for this hub no longer exists.",
                ephemeral=True,
            )
            return

        # ---------- Get roster ----------
        members = [m for m in guild.members if team_role in m.roles]
        if members:
            member_lines = [f"• {m.mention}" for m in members]
        else:
            member_lines = ["*(no players yet)*"]

        # ---------- Get team size from tournament ----------
        t = get_tournament(guild.id)
        team_size = 1
        if t:
            try:
                team_size = int(t.get("team_size") or 1)
            except (TypeError, ValueError):
                team_size = 1

        # ---------- Block READY if team is not full ----------
        if len(members) < team_size:
            await interaction.response.send_message(
                f"❌ Your team must have **{team_size}** players before you can ready up.\n"
                f"Current roster: **{len(members)}/{team_size}**.",
                ephemeral=True,
            )
            return

        # Toggle readiness
        self.ready = not self.ready
        button.label = "✅ Ready" if self.ready else "⏳ Not Ready"

        # ---------- Update DB is_ready flag for this team (for brackets) ----------
        try:
            set_team_ready(guild.id, self.team_role_id, self.ready)
        except Exception:
            log.exception("Failed to update team is_ready flag in DB.")

        # First response: update the hub message's buttons
        await interaction.response.edit_message(view=self)

        # ---------- Find tournament teams channel ----------
        teams_channel: Optional[discord.TextChannel] = None
        if t:
            cid = t.get("teams_channel_id")
            if cid:
                ch = guild.get_channel(cid)
                if isinstance(ch, discord.TextChannel):
                    teams_channel = ch

        if teams_channel is None:
            teams_channel = discord.utils.get(
                guild.text_channels, name="🧾│tournament-teams"
            )

        status_text = "✅ READY" if self.ready else "⏳ NOT READY"
        full_text = "FULL" if len(members) >= team_size else "NOT FULL"
        color = discord.Color.green() if self.ready else discord.Color.orange()

        desc = "\n".join(member_lines)
        desc += (
            f"\n\n**Status:** {status_text}\n"
            f"**Players:** {len(members)}/{team_size} ({full_text})"
        )

        if teams_channel:
            embed = discord.Embed(
                title=f"{status_text} — {team_role.name}",
                description=desc,
                color=color,
            )
            total_teams = 0
            if t:
                try:
                    total_teams = int(t.get("teams_joined") or 0)
                except (TypeError, ValueError):
                    total_teams = 0
            embed.add_field(
                name="Tournament Teams",
                value=f"{total_teams} total teams registered",
                inline=False,
            )
            embed.set_footer(text=f"Updated by {interaction.user} • Team status")
            try:
                await teams_channel.send(embed=embed)
            except Exception:
                log.exception(
                    "Failed to send team status embed to %s",
                    getattr(teams_channel, "id", "unknown"),
                )

        await interaction.followup.send(
            f"Team ready status is now: **{'READY' if self.ready else 'NOT READY'}**",
            ephemeral=True,
        )

    @discord.ui.button(
        label="📄 Match Info",
        style=discord.ButtonStyle.secondary,
        custom_id="t0g_team_matchinfo",
    )
    async def match_info(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            "📄 Match info will appear here once the bracket & matches are generated.",
            ephemeral=True,
        )

    @discord.ui.button(
        label="🗑 Delete Team",
        style=discord.ButtonStyle.danger,
        custom_id="t0g_team_delete",
    )
    async def delete_team(self, interaction: discord.Interaction, button: discord.ui.Button):
        """
        Delete team button.
        - Deletes the team hub channel
        - Deletes the team role
        - Decrements teams_joined
        - Refreshes admin + join panels
        - Removes this team's entries from #tournament-teams
        - Posts a 'this team was deleted' message in the team channel before deletion
        """
        guild = interaction.guild
        if not guild:
            await interaction.response.send_message(
                "❌ This can only be used in a server.",
                ephemeral=True,
            )
            return

        team_role = guild.get_role(self.team_role_id)
        if not team_role:
            await interaction.response.send_message(
                "❌ The team role for this hub no longer exists.",
                ephemeral=True,
            )
            return

        # Only allow team members to delete their team
        if team_role not in interaction.user.roles:
            await interaction.response.send_message(
                "❌ Only members of this team can delete it.",
                ephemeral=True,
            )
            return

        t = get_tournament(guild.id) or {}

        teams_channel: Optional[discord.TextChannel] = None
        cid = t.get("teams_channel_id")
        if cid:
            ch = guild.get_channel(cid)
            if isinstance(ch, discord.TextChannel):
                teams_channel = ch

        if teams_channel is None:
            teams_channel = discord.utils.get(
                guild.text_channels, name="🧾│tournament-teams"
            )

        if teams_channel:
            try:
                async for msg in teams_channel.history(limit=200):
                    delete_me = False
                    if msg.content and str(team_role.mention) in msg.content:
                        delete_me = True
                    for e in msg.embeds:
                        if e.title and team_role.name in e.title:
                            delete_me = True
                    if delete_me:
                        try:
                            await msg.delete()
                        except Exception:
                            continue
            except Exception:
                log.exception(
                    "Failed to clean up team entries in tournament-teams for %s",
                    team_role.id,
                )

        for member in guild.members:
            if team_role in member.roles:
                try:
                    await member.remove_roles(
                        team_role, reason="Tournament team deleted"
                    )
                except discord.Forbidden:
                    log.warning(
                        "Could not remove team role %s from %s in guild %s",
                        team_role.id,
                        member,
                        guild.id,
                    )

        channel = interaction.channel

        await interaction.response.send_message(
            "🗑 Deleting this team, its role, and this channel...",
            ephemeral=True,
        )

        if isinstance(channel, discord.TextChannel):
            try:
                await channel.send(
                    f"🗑 This team (**{team_role.name}**) was deleted by {interaction.user.mention}."
                )
            except Exception:
                pass

        current = t.get("teams_joined") or 0
        try:
            current = int(current)
        except (TypeError, ValueError):
            current = 0
        current = max(0, current - 1)
        t["teams_joined"] = current
        upsert_tournament(guild.id, t)

        try:
            from .tournament_admin_panel import update_panel_message as _update_panel
        except Exception:
            _update_panel = None

        if _update_panel is not None:
            try:
                await _update_panel(guild, t)
            except Exception:
                log.exception("Failed to update admin panel after team deletion.")

        try:
            await refresh_join_panel_message(guild)
        except Exception:
            log.exception("Failed to refresh join panel after team deletion.")

        # Also clear from teams table via helper
        try:
            db_delete_team(guild.id, self.team_role_id)
        except Exception:
            log.exception("Failed to delete team row from teams table.")

        await asyncio.sleep(3)

        try:
            await team_role.delete(
                reason="Tournament team deleted via Delete Team button"
            )
        except discord.Forbidden:
            log.warning(
                "Could not delete team role %s in guild %s",
                team_role.id,
                guild.id,
            )

        if isinstance(channel, discord.TextChannel):
            try:
                await channel.delete(
                    reason="Tournament team hub deleted via Delete Team button"
                )
            except discord.Forbidden:
                log.warning(
                    "Could not delete team channel %s in guild %s",
                    channel.id,
                    guild.id,
                )


# ------------- CALLED BY ADMIN PANEL WHEN CHANNEL IS CREATED -------------


async def setup_create_team_channel(channel: discord.TextChannel) -> None:
    """
    Called from tournament_admin_panel.CreateTournamentModal after the
    #🏷│create-team channel is created.

    Sends the 'Create Team' message with the button.
    """
    embed = discord.Embed(
        title="Create Your Tournament Team",
        description=(
            "Press the button below to create your team.\n\n"
            "When you create a team, the bot will:\n"
            "• Ask for your **team name**\n"
            "• Create a **team role** and give it to you (captain)\n"
            "• Create a **private team hub** channel\n"
            "• Update the **admin panel** and **join panel** with the new team count\n"
            "When you press **Ready** in your team hub, your team & roster will be posted in "
            "the tournament teams list.\n\n"
            "You can also delete your team later from inside the team hub."
        ),
        color=discord.Color.green(),
    )
    view = CreateTeamView()
    await channel.send(embed=embed, view=view)


# ------------- REQUIRED FOR AUTO-LOADER -------------


async def setup(bot: commands.Bot):
    # No Cog class to add; this file just provides helpers & views.
    log.info("tournament_create_team_cog loaded (create-team helpers).")
