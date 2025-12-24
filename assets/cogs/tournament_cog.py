# cogs/tournament_cog.py
import logging

import discord
from discord import app_commands
from discord.ext import commands

from .tournament_db import init_db, DB_PATH
from .tournament_admin_panel import CreateTournamentModal

log = logging.getLogger(__name__)


class TournamentCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        init_db()
        log.info("Tournament DB initialized at %s", DB_PATH)

    @app_commands.command(
        name="create_tournament",
        description="Create a new tournament (Server Owner or Admin only)."
    )
    async def create_tournament(self, interaction: discord.Interaction):
        guild = interaction.guild
        if not guild:
            await interaction.response.send_message(
                "❌ This command can only be used in a server.",
                ephemeral=True
            )
            return

        user = interaction.user

        # Allow Server Owner or Manage Server admins
        if user.id != guild.owner_id and not user.guild_permissions.manage_guild:
            await interaction.response.send_message(
                "❌ Only **Server Owner** or **Admins (Manage Server)** can create tournaments.",
                ephemeral=True
            )
            return

        # No channel restriction anymore – can run anywhere
        modal = CreateTournamentModal(self)
        await interaction.response.send_modal(modal)

    @create_tournament.error
    async def create_tournament_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        log.exception("Error in /create_tournament: %r", error)
        if interaction.response.is_done():
            await interaction.followup.send("❌ Something went wrong.", ephemeral=True)
        else:
            await interaction.response.send_message("❌ Something went wrong.", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(TournamentCog(bot))
