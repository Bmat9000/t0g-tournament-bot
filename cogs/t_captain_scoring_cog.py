import logging

import discord
from discord import app_commands
from discord.ext import commands

from .tournament_db import get_tournament, upsert_tournament
from .tournament_admin_panel import update_panel_message, refresh_join_panel_message

log = logging.getLogger(__name__)


class TournamentCaptainScoringCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        log.info("TournamentCaptainScoringCog loaded.")

    @app_commands.command(
        name="t_captain_scoring",
        description="Toggle captain scoring ON/OFF.",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def t_captain_scoring(self, interaction: discord.Interaction):
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
                "❌ No active tournament found.",
                ephemeral=True,
            )
            return

        t["captain_scoring"] = 0 if t["captain_scoring"] else 1
        upsert_tournament(guild.id, t)

        state = "ON (Captains + Admins)" if t["captain_scoring"] else "OFF (Admins Only)"
        log.info(
            "Guild %s: Captain scoring toggled to %s by %s",
            guild.id,
            state,
            interaction.user,
        )

        await update_panel_message(guild, t)
        await refresh_join_panel_message(guild)

        await interaction.response.send_message(
            f"✅ Captain Scoring set to **{state}**.",
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(TournamentCaptainScoringCog(bot))
