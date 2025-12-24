# cogs/tournament_announcements_cog.py
import logging

import discord
from discord.ext import commands

log = logging.getLogger(__name__)


async def setup_tournament_announcements_channel(channel: discord.TextChannel) -> None:
    """
    Initialize 📢│tournament-announcements.
    Later we can add fancy pinned messages, rules, etc.
    """
    log.info("Initializing announcements channel %s in guild %s", channel.id, channel.guild.id)

    await channel.send(
        "📢 **Tournament Announcements**\n"
        "All official updates for this tournament will be posted here.\n"
        "Only staff can speak in this channel."
    )


async def setup(bot: commands.Bot):
    # Nothing persistent here yet; we just log so the extension loads cleanly.
    log.info("tournament_announcements_cog loaded.")
