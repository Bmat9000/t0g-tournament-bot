import logging

import discord
from discord import app_commands
from discord.ext import commands

from .tournament_db import get_tournament
from .tournament_admin_panel import DeleteTournamentModal

log = logging.getLogger(__name__)


class TournamentDeleteTournamentCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        log.info("TournamentDeleteTournamentCog loaded.")

    @app_commands.command(
        name="t_delete_tournament",
        description="Open a confirmation modal to delete the current tournament.",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def t_delete_tournament(self, interaction: discord.Interaction):
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

        modal = DeleteTournamentModal(guild.id)
        await interaction.response.send_modal(modal)


async def setup(bot: commands.Bot):
    await bot.add_cog(TournamentDeleteTournamentCog(bot))
