import logging

import discord
from discord import app_commands
from discord.ext import commands

from .tournament_db import get_tournament, upsert_tournament
from .tournament_admin_panel import update_panel_message
from .tournament_admin_panel import refresh_join_panel_message  # defined via try/except there

log = logging.getLogger(__name__)


class TournamentToggleBracketCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        log.info("TournamentToggleBracketCog loaded.")

    @app_commands.command(
        name="t_toggle_bracket",
        description="Toggle the tournament bracket type (Single / Double Elim).",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def t_toggle_bracket(self, interaction: discord.Interaction):
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

        t["bracket_type"] = "Double Elim" if t["bracket_type"] == "Single Elim" else "Single Elim"
        upsert_tournament(guild.id, t)

        log.info(
            "Guild %s: Bracket type toggled to %s by %s",
            guild.id,
            t["bracket_type"],
            interaction.user,
        )

        await update_panel_message(guild, t)
        await refresh_join_panel_message(guild)

        await interaction.response.send_message(
            f"✅ Bracket Type set to **{t['bracket_type']}**.",
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(TournamentToggleBracketCog(bot))
