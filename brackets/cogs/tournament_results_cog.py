# cogs/tournament_results_cog.py
import logging

import discord
from discord.ext import commands

log = logging.getLogger(__name__)


async def setup_match_results_channel(channel: discord.TextChannel) -> None:
    """
    Initialize 🎯│match-results.
    """
    log.info("Initializing match-results channel %s in guild %s", channel.id, channel.guild.id)

    await channel.send(
        "🎯 **Match Results**\n"
        "After each match, staff (or captains, depending on your rules) "
        "can report scores here.\n"
        "_Automatic reporting flow will be added later._"
    )


async def setup(bot: commands.Bot):
    log.info("tournament_results_cog loaded.")
