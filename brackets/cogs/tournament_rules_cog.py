# cogs/tournament_rules_cog.py
import logging

import discord
from discord.ext import commands

log = logging.getLogger(__name__)


async def setup_tournament_rules_channel(channel: discord.TextChannel) -> None:
    """
    Initialize 📜│tournament-rules.
    """
    log.info("Initializing rules channel %s in guild %s", channel.id, channel.guild.id)

    embed = discord.Embed(
        title="📜 Tournament Rules",
        description=(
            "Here you can add the rules for your tournament.\n\n"
            "Suggested sections:\n"
            "• Format (2v2, 3v3, etc.)\n"
            "• Map / mode rules\n"
            "• No-cheating / fair play rules\n"
            "• Host / server settings\n"
            "• Reporting scores & screenshots\n"
        ),
        color=discord.Color.orange(),
    )
    await channel.send(embed=embed)


async def setup(bot: commands.Bot):
    log.info("tournament_rules_cog loaded.")
