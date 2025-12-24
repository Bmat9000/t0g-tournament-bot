# cogs/t_start_bracket_cog.py

import io
import asyncio
import logging
import math
from typing import List, Optional, Dict, Any

import discord
from discord import app_commands
from discord.ext import commands
from discord.ui import Modal, TextInput, View, button

from .tournament_db import (
    get_tournament,
    upsert_tournament,
    get_ready_teams,
    clear_bracket,
    insert_bracket_match,
    update_bracket_match,
    get_db_connection,
)
from .tournament_admin_panel import update_panel_message
from .tournament_bracket_cog import get_seeded_teams, draw_bracket_image

log = logging.getLogger(__name__)


# -------------------------------------------------------------------
# helper: delete match channel shortly after scoring
# -------------------------------------------------------------------

async def _delete_channel_later(channel: discord.abc.GuildChannel, delay: int = 5):
    await asyncio.sleep(delay)
    try:
        await channel.delete(reason="T0G Tournament: match completed, channel cleanup.")
    except Exception as e:
        log.warning(
            "Failed to delete match channel %s: %r",
            getattr(channel, "id", "?"),
            e,
        )


# -------------------------------------------------------------------
# SCORE MODAL + BUTTON
# -------------------------------------------------------------------

class ScoreMatchModal(Modal):
    """Modal used to enter the final score for a match."""

    def __init__(self, guild_id: int, match_id: int, team_a: str, team_b: str):
        super().__init__(title=f"Score Match #{match_id}")

        self.guild_id = guild_id
        self.match_id = match_id
        self.team_a = team_a
        self.team_b = team_b

        self.score_a = TextInput(
            label=f"{team_a} score",
            placeholder="1",
            required=True,
            max_length=3,
        )
        self.score_b = TextInput(
            label=f"{team_b} score",
            placeholder="0",
            required=True,
            max_length=3,
        )

        self.add_item(self.score_a)
        self.add_item(self.score_b)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        # 1) parse scores
        try:
            s_a = int(str(self.score_a.value).strip())
            s_b = int(str(self.score_b.value).strip())
        except ValueError:
            await interaction.response.send_message(
                "❌ Scores must be whole numbers.",
                ephemeral=True,
            )
            return

        if s_a == s_b:
            await interaction.response.send_message(
                "❌ Scores cannot be tied. Please enter a winner.",
                ephemeral=True,
            )
            return

        winner = self.team_a if s_a > s_b else self.team_b

        # 2) save to DB
        try:
            update_bracket_match(
                self.guild_id,
                self.match_id,
                winner=winner,
                status="COMPLETED",
            )
        except Exception as e:
            log.exception(
                "Failed to update bracket match %s for guild %s: %r",
                self.match_id,
                self.guild_id,
                e,
            )
            await interaction.response.send_message(
                "❌ Failed to save match result (check logs).",
                ephemeral=True,
            )
            return

        guild = interaction.guild
        channel = interaction.channel

        # 3) result embed
        result_embed = discord.Embed(
            title=f"Match {self.match_id} Result",
            description=(
                f"**{self.team_a}** score: **{s_a}**\n"
                f"**{self.team_b}** score: **{s_b}**\n\n"
                f"🏆 **Winner: {winner}**"
            ),
            colour=discord.Colour.from_rgb(201, 0, 43),
        )

        await interaction.response.send_message(embed=result_embed)

        # 4) mirror result into match-results + bracket-and-scores channels
        if guild is not None:
            # match-results
            results_ch = discord.utils.find(
                lambda c: isinstance(c, discord.TextChannel)
                and "match-results" in c.name,
                guild.text_channels,
            )
            if results_ch:
                try:
                    await results_ch.send(embed=result_embed)
                except Exception as e:
                    log.warning(
                        "Failed to send result to match-results in guild %s: %r",
                        guild.id,
                        e,
                    )

            # bracket-and-scores
            bracket_ch = discord.utils.find(
                lambda c: isinstance(c, discord.TextChannel)
                and ("bracket-and-scores" in c.name or "bracket" in c.name),
                guild.text_channels,
            )
            if bracket_ch:
                try:
                    await bracket_ch.send(
                        f"📊 **Match {self.match_id} Result:** "
                        f"**{self.team_a}** {s_a} – {s_b} **{self.team_b}** "
                        f"(winner: **{winner}**)"
                    )
                except Exception as e:
                    log.warning(
                        "Failed to send result to bracket channel in guild %s: %r",
                        guild.id,
                        e,
                    )

        # 5) clean up match channel
        if isinstance(channel, discord.TextChannel):
            try:
                await channel.send(
                    "✅ Match scored. This channel will be deleted in **5 seconds**."
                )
                asyncio.create_task(_delete_channel_later(channel, 5))
            except Exception as e:
                log.warning(
                    "Failed to schedule deletion message for channel %s: %r",
                    channel.id if channel else "?",
                    e,
                )

        # 6) tell the main cog to progress bracket / create next round / update image
        if guild is not None:
            cog = interaction.client.get_cog("TournamentStartBracketCog")
            if cog is not None:
                try:
                    await cog.after_match_scored(guild)
                except Exception as e:
                    log.exception(
                        "after_match_scored failed for guild %s: %r",
                        guild.id,
                        e,
                    )


class ScoreMatchView(View):
    """View that puts the Score Match button under the match message."""

    def __init__(self, guild_id: int, match_id: int, team_a: str, team_b: str):
        super().__init__(timeout=None)
        self.guild_id = guild_id
        self.match_id = match_id
        self.team_a = team_a
        self.team_b = team_b

    @button(label="Score Match", style=discord.ButtonStyle.primary, emoji="📊")
    async def score_match(
        self,
        interaction: discord.Interaction,
        btn: discord.ui.Button,
    ):
        # for now: staff only (manage_guild)
        if not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message(
                "❌ Only tournament staff can score matches right now.",
                ephemeral=True,
            )
            return

        modal = ScoreMatchModal(
            guild_id=self.guild_id,
            match_id=self.match_id,
            team_a=self.team_a,
            team_b=self.team_b,
        )
        await interaction.response.send_modal(modal)


# -------------------------------------------------------------------
# MAIN COG
# -------------------------------------------------------------------

class TournamentStartBracketCog(commands.Cog):
    """Commands to start the tournament, generate/update bracket, and auto-create rounds."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        log.info("TournamentStartBracketCog loaded.")

    # ---------------- ROUND CREATION HELPERS -----------------

    async def _get_or_create_matches_category(
        self,
        guild: discord.Guild,
        t: Dict[str, Any],
        recreate_reason: Optional[str] = None,
    ) -> discord.CategoryChannel:
        """
        Reuse existing matches category if it exists, otherwise create it.
        This avoids creating duplicate categories every time matches are made.
        """
        matches_category: Optional[discord.CategoryChannel] = None
        target_name = f"🎯 {t.get('name', 'Tournament')} Matches"

        # Try stored ID first (if it was ever set in-memory)
        matches_category_id = t.get("matches_category_id")
        if matches_category_id:
            ch = guild.get_channel(matches_category_id)
            if isinstance(ch, discord.CategoryChannel):
                matches_category = ch

        # If not found by ID, try by name
        if matches_category is None:
            matches_category = discord.utils.get(guild.categories, name=target_name)

        # If still not found, create a new category
        if matches_category is None:
            base_cat: Optional[discord.CategoryChannel] = None
            base_cat_id = t.get("category_id")
            if base_cat_id:
                tmp = guild.get_channel(base_cat_id)
                if isinstance(tmp, discord.CategoryChannel):
                    base_cat = tmp

            reason = recreate_reason or "T0G Tournament: matches category"
            matches_category = await guild.create_category(
                target_name,
                reason=reason,
            )

            # try to move right under base category for clean layout
            if base_cat is not None:
                try:
                    await matches_category.move(after=base_cat)
                except Exception:
                    pass

            # cache in-memory (DB doesn't store this column, but no harm keeping in dict)
            t["matches_category_id"] = matches_category.id
            upsert_tournament(guild.id, t)
            log.info(
                "Created matches category %r (%s) for guild %s",
                matches_category.name,
                matches_category.id,
                guild.id,
            )

        return matches_category

    async def _create_round_one_matches(
        self,
        guild: discord.Guild,
        t: Dict[str, Any],
    ) -> int:
        """Create round-1 matches from READY/BOT teams using seeding order."""
        seeded = get_seeded_teams(guild)
        team_count = len(seeded)

        if team_count < 2:
            log.warning(
                "create_round_one_matches: not enough teams for guild %s", guild.id
            )
            return 0
        if team_count % 2 != 0:
            log.warning(
                "create_round_one_matches: team count (%s) is not even", team_count
            )
            return 0

        best_of = t.get("best_of", 1)

        ready_rows = get_ready_teams(guild.id)
        by_name: Dict[str, dict] = {row["team_name"]: row for row in ready_rows}

        # -------- MATCHES CATEGORY (reuse if exists) --------
        matches_category = await self._get_or_create_matches_category(guild, t)

        # wipe any old bracket for this tournament
        clear_bracket(guild.id)

        # find current max match_id (should be 0 after clear, but safe)
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute(
            "SELECT COALESCE(MAX(match_id), 0) FROM bracket_matches WHERE guild_id = ?",
            (guild.id,),
        )
        max_id = cur.fetchone()[0] or 0
        conn.close()

        match_index = max_id + 1
        created = 0

        for i in range(0, team_count, 2):
            team_a = seeded[i]
            team_b = seeded[i + 1]

            row_a = by_name.get(team_a)
            row_b = by_name.get(team_b)

            overwrites = {
                guild.default_role: discord.PermissionOverwrite(
                    view_channel=False,
                    send_messages=False,
                    read_message_history=False,
                )
            }

            def add_team_perms(row: Optional[dict]):
                if not row:
                    return
                # sqlite3.Row supports dict-style indexing, but not .get()
                try:
                    role_id = row["role_id"]
                except Exception:
                    role_id = 0
                if role_id:
                    role = guild.get_role(role_id)
                    if role:
                        overwrites[role] = discord.PermissionOverwrite(
                            view_channel=True,
                            send_messages=True,
                            read_message_history=True,
                            attach_files=True,
                        )

            add_team_perms(row_a)
            add_team_perms(row_b)

            safe_a = team_a.lower().replace(" ", "-")
            safe_b = team_b.lower().replace(" ", "-")
            channel_name = f"match-{match_index}-{safe_a}-vs-{safe_b}"

            match_channel = await matches_category.create_text_channel(
                channel_name,
                overwrites=overwrites,
                reason="T0G Tournament round-1 match channel",
            )

            desc = (
                f"📣 **Match Started!** **{team_a}** vs **{team_b}**\n\n"
                f"Bracket Match **#{match_index}**, **Best-of-{best_of}**.\n\n"
                "Use the **Score Match** button below to submit rounds won.\n"
                "Captains can score only if Captain Scoring is ON, otherwise admins only.\n"
                "No ties allowed. Results will be posted in **#bracket-and-scores**, "
                "winner advances, and this channel will be deleted shortly after scoring."
            )

            embed = discord.Embed(
                title=f"Match {match_index}: {team_a} vs {team_b}",
                description=desc,
                colour=discord.Colour.from_rgb(201, 0, 43),
            )

            view = ScoreMatchView(
                guild_id=guild.id,
                match_id=match_index,
                team_a=team_a,
                team_b=team_b,
            )
            await match_channel.send(embed=embed, view=view)

            insert_bracket_match(
                guild_id=guild.id,
                match_id=match_index,
                round_number=1,
                team_a=team_a,
                team_b=team_b,
                winner=None,
                status="PENDING",
                channel_id=match_channel.id,
            )

            log.info(
                "Created match %s in guild %s: %s vs %s (channel %s)",
                match_index,
                guild.id,
                team_a,
                team_b,
                match_channel.id,
            )

            match_index += 1
            created += 1

        return created

    async def _create_next_round_matches(
        self,
        guild: discord.Guild,
        t: Dict[str, Any],
        round_number: int,
        teams: List[str],
    ) -> int:
        """
        Create next-round matches from a list of winner team names.
        round_number starts at 2 for semi-finals, etc.
        """
        team_count = len(teams)
        if team_count < 2:
            return 0
        if team_count % 2 != 0:
            log.warning(
                "create_next_round_matches: team count (%s) is not even for round %s",
                team_count,
                round_number,
            )
            return 0

        best_of = t.get("best_of", 1)

        ready_rows = get_ready_teams(guild.id)
        by_name: Dict[str, dict] = {row["team_name"]: row for row in ready_rows}

        # use the same matches category we created earlier (reuse if it exists)
        matches_category = await self._get_or_create_matches_category(
            guild,
            t,
            recreate_reason=f"T0G Tournament: round-{round_number} matches category recreate",
        )

        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute(
            "SELECT COALESCE(MAX(match_id), 0) FROM bracket_matches WHERE guild_id = ?",
            (guild.id,),
        )
        max_id = cur.fetchone()[0] or 0
        conn.close()

        match_index = max_id + 1
        created = 0

        for i in range(0, team_count, 2):
            team_a = teams[i]
            team_b = teams[i + 1]

            row_a = by_name.get(team_a)
            row_b = by_name.get(team_b)

            overwrites = {
                guild.default_role: discord.PermissionOverwrite(
                    view_channel=False,
                    send_messages=False,
                    read_message_history=False,
                )
            }

            def add_team_perms(row: Optional[dict]):
                if not row:
                    return
                try:
                    role_id = row["role_id"]
                except Exception:
                    role_id = 0
                if role_id:
                    role = guild.get_role(role_id)
                    if role:
                        overwrites[role] = discord.PermissionOverwrite(
                            view_channel=True,
                            send_messages=True,
                            read_message_history=True,
                            attach_files=True,
                        )

            add_team_perms(row_a)
            add_team_perms(row_b)

            safe_a = team_a.lower().replace(" ", "-")
            safe_b = team_b.lower().replace(" ", "-")
            channel_name = f"match-{match_index}-{safe_a}-vs-{safe_b}"

            match_channel = await matches_category.create_text_channel(
                channel_name,
                overwrites=overwrites,
                reason=f"T0G Tournament round-{round_number} match channel",
            )

            desc = (
                f"📣 **Match Started!** **{team_a}** vs **{team_b}**\n\n"
                f"Bracket Match **#{match_index}**, **Best-of-{best_of}**.\n\n"
                "Use the **Score Match** button below to submit rounds won.\n"
                "Captains can score only if Captain Scoring is ON, otherwise admins only.\n"
                "No ties allowed. Results will be posted in **#bracket-and-scores**, "
                "winner advances, and this channel will be deleted shortly after scoring."
            )

            embed = discord.Embed(
                title=f"Match {match_index}: {team_a} vs {team_b}",
                description=desc,
                colour=discord.Colour.from_rgb(201, 0, 43),
            )

            view = ScoreMatchView(
                guild_id=guild.id,
                match_id=match_index,
                team_a=team_a,
                team_b=team_b,
            )
            await match_channel.send(embed=embed, view=view)

            insert_bracket_match(
                guild_id=guild.id,
                match_id=match_index,
                round_number=round_number,
                team_a=team_a,
                team_b=team_b,
                winner=None,
                status="PENDING",
                channel_id=match_channel.id,
            )

            log.info(
                "Created match %s (round %s) in guild %s: %s vs %s (channel %s)",
                match_index,
                round_number,
                guild.id,
                team_a,
                team_b,
                match_channel.id,
            )

            match_index += 1
            created += 1

        return created

    # ---------------- BRACKET IMAGE UPDATE -----------------

    async def _update_bracket_image(self, guild: discord.Guild):
        """
        Re-draw the bracket image using the original seeded team list.

        Columns:
        - column 0 = original seeds (Round 1 participants)
        - column 1 = winners of Round 1 (participants in Round 2)
        - column 2 = winners of Round 2, etc.

        As soon as a match is completed in any round:
        - the loser gets X'ed only in the column they lost in
        - the winner is shown in the next column, even if other matches
          in that round are not done yet.
        """
        # full seeded list (original order)
        seeds = get_seeded_teams(guild)
        if not seeds:
            return
        if len(seeds) not in (2, 4, 8, 16, 32):
            return

        n = len(seeds)

        # read all matches for this guild
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT round_number, match_id, team_a, team_b, winner, status
            FROM bracket_matches
            WHERE guild_id = ?
            ORDER BY round_number ASC, match_id ASC
            """,
            (guild.id,),
        )
        rows = cur.fetchall()
        conn.close()

        if not rows:
            # no matches yet; nothing to update
            return

        # group rows by round_number
        round_map: Dict[int, list] = {}
        for row in rows:
            rnd = row["round_number"]
            round_map.setdefault(rnd, []).append(row)

        max_round = max(round_map.keys())

        # ---------- Build advancing_by_round ----------
        # column 0 = seeds
        advancing_by_round: List[List[Optional[str]]] = []
        advancing_by_round.append(list(seeds))

        # each next column r = winners of round r (by match_id order)
        loss_round: Dict[str, int] = {}

        for rnd in range(1, max_round + 1):
            r_rows = round_map.get(rnd, [])
            # make sure they're in match_id order
            r_rows = sorted(r_rows, key=lambda r: r["match_id"])

            winners_for_round: List[Optional[str]] = []

            for r in r_rows:
                team_a = r["team_a"]
                team_b = r["team_b"]
                winner = r["winner"]
                status_str = (r["status"] or "").upper()

                if status_str == "COMPLETED" and winner and winner in (team_a, team_b):
                    winners_for_round.append(winner)

                    # record where the LOSER lost (earliest round wins)
                    loser = team_b if winner == team_a else team_a
                    if loser and (loser not in loss_round or rnd < loss_round[loser]):
                        loss_round[loser] = rnd
                else:
                    # match not completed yet -> winner unknown
                    winners_for_round.append(None)

            advancing_by_round.append(winners_for_round)

        # ---------- Determine which slots to X out ----------
        # For each team, X the box in the column that corresponds to
        # the round they lost in (round 1 => column 0, round 2 => column 1, etc.)
        eliminated_slots: List[tuple] = []

        for team, rnd in loss_round.items():
            col_idx = rnd - 1  # round 1 -> column 0, round 2 -> column 1, ...
            if col_idx < 0 or col_idx >= len(advancing_by_round):
                continue

            col = advancing_by_round[col_idx]

            try:
                slot_index = col.index(team)
            except ValueError:
                # should not normally happen, but skip if it does
                continue

            eliminated_slots.append((col_idx, slot_index))

        t = get_tournament(guild.id)
        if not t:
            return

        # find or remember bracket channel
        bracket_channel: Optional[discord.TextChannel] = None
        bracket_channel_id = t.get("bracket_channel_id") or 0
        if bracket_channel_id:
            ch = guild.get_channel(bracket_channel_id)
            if isinstance(ch, discord.TextChannel):
                bracket_channel = ch

        if bracket_channel is None:
            guess = discord.utils.find(
                lambda c: isinstance(c, discord.TextChannel)
                and ("bracket-and-scores" in c.name or "bracket" in c.name),
                guild.text_channels,
            )
            if guess:
                bracket_channel = guess
                t["bracket_channel_id"] = guess.id
                upsert_tournament(guild.id, t)

        if bracket_channel is None:
            return

        # delete old bracket message
        try:
            async for msg in bracket_channel.history(limit=50):
                if msg.author == guild.me and (
                    (msg.content or "").startswith("🧾 Tournament Bracket")
                    or any(
                        a.filename.endswith("tournament_bracket.png")
                        for a in msg.attachments
                    )
                ):
                    await msg.delete()
                    break
        except Exception as e:
            log.warning(
                "Failed to delete old bracket image in guild %s: %r",
                guild.id,
                e,
            )

        # draw updated bracket (winners pushed forward, losers X'ed once)
        try:
            png_bytes = draw_bracket_image(
                seeds,
                eliminated_slots=eliminated_slots,
                advancing_by_round=advancing_by_round,
            )
        except Exception as e:
            log.exception(
                "Error drawing updated bracket image for guild %s: %r",
                guild.id,
                e,
            )
            return

        file = discord.File(io.BytesIO(png_bytes), filename="tournament_bracket.png")
        content = f"🧾 Tournament Bracket (**{len(seeds)}** teams)"
        await bracket_channel.send(content=content, file=file)


    # ---------------- AFTER MATCH SCORED -----------------

    async def after_match_scored(self, guild: discord.Guild):
        """
        Called every time a match is scored.
        - Updates the bracket image (X'ing out losers, drawing winners in next round).
        - When an entire round is completed, creates the next round.
        - When only 1 team remains, marks tournament as FINISHED and announces winner.
        """
        conn = get_db_connection()
        cur = conn.cursor()

        # current highest round
        cur.execute(
            "SELECT MAX(round_number) FROM bracket_matches WHERE guild_id = ?",
            (guild.id,),
        )
        row = cur.fetchone()
        if not row or row[0] is None:
            conn.close()
            return

        current_round = row[0]

        cur.execute(
            """
            SELECT match_id, team_a, team_b, winner, status
            FROM bracket_matches
            WHERE guild_id = ? AND round_number = ?
            ORDER BY match_id ASC
            """,
            (guild.id, current_round),
        )
        rows = cur.fetchall()
        conn.close()

        if not rows:
            return

        # who has won in this round so far?
        winners = [
            r["winner"]
            for r in rows
            if (r["status"] or "").upper() == "COMPLETED" and r["winner"]
        ]

        all_completed = all((r["status"] or "").upper() == "COMPLETED" for r in rows)

        # always refresh the bracket image so losers get X'ed and winners move down
        await self._update_bracket_image(guild)

        t = get_tournament(guild.id)
        if not t:
            return

        # if the round isn't done yet, stop here
        if not all_completed:
            return

        # if we ended up with a single winner, tournament is over
        if len(winners) == 1:
            champion = winners[0]

            # mark FINISHED and refresh panel
            t["status"] = "FINISHED"
            upsert_tournament(guild.id, t)
            await update_panel_message(guild, t)

            # final bracket image already updated above; announce champ
            bracket_ch = None
            bracket_channel_id = t.get("bracket_channel_id") or 0
            if bracket_channel_id:
                ch = guild.get_channel(bracket_channel_id)
                if isinstance(ch, discord.TextChannel):
                    bracket_ch = ch

            if bracket_ch is None:
                bracket_ch = discord.utils.find(
                    lambda c: isinstance(c, discord.TextChannel)
                    and ("bracket-and-scores" in c.name or "bracket" in c.name),
                    guild.text_channels,
                )

            if bracket_ch:
                await bracket_ch.send(f"🏆 **Tournament Winner:** **{champion}**")

            return

        # otherwise, create the next round if it doesn't exist yet
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT COUNT(*) FROM bracket_matches
            WHERE guild_id = ? AND round_number = ?
            """,
            (guild.id, current_round + 1),
        )
        next_count = cur.fetchone()[0]
        conn.close()

        if next_count == 0:
            await self._create_next_round_matches(
                guild, t, current_round + 1, winners
            )

    # ---------------- SLASH COMMANDS -----------------

    @app_commands.command(
        name="start_tournament",
        description="Mark the current tournament as RUNNING and create round-one match channels.",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def start_tournament(self, interaction: discord.Interaction):
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message(
                "❌ This command can only be used in a server.",
                ephemeral=True,
            )
            return

        t = get_tournament(guild.id)
        if not t:
            await interaction.response.send_message(
                "❌ No tournament found. Create one first.",
                ephemeral=True,
            )
            return

        current_status = t.get("status", "WAITING")
        if current_status == "RUNNING":
            await interaction.response.send_message(
                "⚠️ Tournament is already marked as **RUNNING**.\n"
                "If you need to remake matches, clear them manually first.",
                ephemeral=True,
            )
            return
        if current_status == "FINISHED":
            await interaction.response.send_message(
                "⚠️ Tournament is already **FINISHED**.",
                ephemeral=True,
            )
            return

        # defer so we don't hit "Unknown interaction" when creating a bunch of channels
        await interaction.response.defer(ephemeral=True, thinking=True)

        # set status + update panel
        t["status"] = "RUNNING"
        upsert_tournament(guild.id, t)
        await update_panel_message(guild, t)

        created = await self._create_round_one_matches(guild, t)

        if created == 0:
            msg = (
                "✅ Tournament status set to **RUNNING**, but I couldn't create any matches.\n"
                "Make sure you have at least **2 READY** teams."
            )
        else:
            msg = (
                f"✅ Tournament status set to **RUNNING**.\n"
                f"Created **{created}** match channel(s) for Round 1."
            )

        await interaction.followup.send(msg, ephemeral=True)

    @app_commands.command(
        name="generate_bracket",
        description="Generate and post the tournament bracket image (from READY teams).",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def generate_bracket(self, interaction: discord.Interaction):
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message(
                "❌ This command can only be used in a server.",
                ephemeral=True,
            )
            return

        t = get_tournament(guild.id)
        if not t:
            await interaction.response.send_message(
                "❌ No tournament found. Create one first.",
                ephemeral=True,
            )
            return

        seeded = get_seeded_teams(guild)
        count = len(seeded)

        if count < 2:
            await interaction.response.send_message(
                "❌ Not enough teams to build a bracket. You need at least **2** READY teams.",
                ephemeral=True,
            )
            return

        if count not in (2, 4, 8, 16, 32):
            await interaction.response.send_message(
                "❌ Bracket size must be **2, 4, 8, 16, or 32** ready teams.\n"
                f"Currently detected: **{count}**.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=False, thinking=True)

        try:
            # initial bracket: no eliminated teams, no winners yet
            png_bytes = draw_bracket_image(seeded)
        except Exception as e:
            log.exception(
                "Error drawing bracket image for guild %s: %r",
                guild.id,
                e,
            )
            await interaction.followup.send(
                "❌ Could not generate bracket image (check logs)."
            )
            return

        # find / remember bracket channel
        bracket_channel: Optional[discord.TextChannel] = None
        bracket_channel_id = t.get("bracket_channel_id") or 0
        if bracket_channel_id:
            ch = guild.get_channel(bracket_channel_id)
            if isinstance(ch, discord.TextChannel):
                bracket_channel = ch

        if bracket_channel is None:
            guess = discord.utils.find(
                lambda c: isinstance(c, discord.TextChannel)
                and ("bracket-and-scores" in c.name or "bracket" in c.name),
                guild.text_channels,
            )
            if guess:
                bracket_channel = guess
                t["bracket_channel_id"] = guess.id
                upsert_tournament(guild.id, t)

        target_channel: discord.abc.Messageable = bracket_channel or interaction.channel

        file = discord.File(io.BytesIO(png_bytes), filename="tournament_bracket.png")
        content = f"🧾 Tournament Bracket (**{count}** teams)"
        await target_channel.send(content=content, file=file)

        if target_channel.id != interaction.channel_id:
            await interaction.followup.send(
                f"✅ Bracket generated and posted in {target_channel.mention}.",
                ephemeral=True,
            )


# -------------------------------------------------------------------
# SETUP
# -------------------------------------------------------------------

async def setup(bot: commands.Bot):
    await bot.add_cog(TournamentStartBracketCog(bot))
