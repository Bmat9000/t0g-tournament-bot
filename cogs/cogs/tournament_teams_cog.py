# cogs/tournament_teams_cog.py
import logging

import discord
from discord.ext import commands

log = logging.getLogger(__name__)


async def setup_tournament_teams_channel(channel: discord.TextChannel) -> None:
    """
    Initialize 🧾│tournament-teams.
    Eventually this will show dynamic team list.
    """
    log.info("Initializing teams channel %s in guild %s", channel.id, channel.guild.id)

    await channel.send(
        "🧾 **Tournament Teams**\n"
        "Teams that join the tournament will be listed here.\n"
        "_Team listing system coming soon._"
    )


async def setup(bot: commands.Bot):
    log.info("tournament_teams_cog loaded.")
