import logging

import discord
from discord import app_commands
from discord.ext import commands

from .tournament_db import get_tournament, upsert_tournament
from .tournament_admin_panel import update_panel_message, refresh_join_panel_message

log = logging.getLogger(__name__)


class TournamentCloseJoinCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        log.info("TournamentCloseJoinCog loaded.")

    @app_commands.command(
        name="t_close_join",
        description="Close the tournament join queue.",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def t_close_join(self, interaction: discord.Interaction):
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

        t["queue_status"] = "CLOSED"
        upsert_tournament(guild.id, t)
        log.info("Guild %s: Queue closed by %s", guild.id, interaction.user)

        await update_panel_message(guild, t)
        await refresh_join_panel_message(guild)

        await interaction.response.send_message(
            "✅ Join is now **CLOSED**.",
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(TournamentCloseJoinCog(bot))
