"""Bot entrypoint.

Cleanup Roadmap v1: Centralize config & paths (no behavior changes).

- Loads .env exactly once via core.config
- Uses ROOT-based paths for consistency
"""

from core.config import ROOT, env  # loads .env once
from core.logging_setup import setup_logging

import discord
from discord.ext import commands

# ---------- Logging Setup ----------
log = setup_logging()

# ---------- Env & Token ----------
TOKEN = env("DISCORD_TOKEN")
if not TOKEN:
    log.critical("DISCORD_TOKEN not found in .env file – cannot start bot.")
    raise RuntimeError("DISCORD_TOKEN not found in .env file")

# ---------- Intents ----------
intents = discord.Intents.default()
intents.members = True      # we'll need member info for teams later
intents.guilds = True

# ---------- Paths ----------
COGS_DIR = ROOT / "cogs"


# ---------- Bot Class ----------
class T0GTournamentBot(commands.Bot):
    def __init__(self):
        super().__init__(
            command_prefix="!",
            intents=intents,
            application_id=None,  # optional, Discord will infer it
        )
        log.info("Bot instance created.")

    async def setup_hook(self):
        """
        Runs before the bot connects to Discord.
        Good place to load cogs and sync app commands.
        """
        log.info("Running setup_hook...")

        # -------- Auto-discover and load all cogs in /cogs --------
        if not COGS_DIR.exists():
            log.warning("Cogs directory %s does not exist.", COGS_DIR.resolve())
        else:
            log.info("Searching for cogs in %s ...", COGS_DIR.resolve())

        for path in COGS_DIR.glob("*.py"):
            # Skip dunder/hidden files like __init__.py
            if path.name.startswith("_"):
                continue

            ext_name = f"cogs.{path.stem}"

            try:
                await self.load_extension(ext_name)
                # ✅ successful load
                log.info("✅ Loaded cog: %s", ext_name)
            except Exception as e:
                # ❌ failed load – log full error + reason
                log.exception("❌ Failed to load cog: %s - %s", ext_name, e)

        # Sync slash commands
        try:
            log.info("Syncing application (slash) commands globally...")
            synced = await self.tree.sync()
            log.info("Slash commands synced. Total commands: %d", len(synced))
        except Exception as e:
            log.exception("Error while syncing application commands: %s", e)

        log.info("setup_hook finished.")

    async def on_ready(self):
        """
        Called when the bot has connected and is ready.
        """
        log.info("Logged in as %s (ID: %s)", self.user, self.user.id)
        log.info("T0G Tournament Bot is online and ready.")
        await self.change_presence(
            activity=discord.Game(name="T0G Tournaments | /create_tournament")
        )


bot = T0GTournamentBot()


# ---------- Global Error Handlers ----------

@bot.event
async def on_error(event_method, *args, **kwargs):
    """
    Catches errors in Discord events that aren't otherwise handled.
    """
    log.exception(f"Unhandled error in event '{event_method}'", exc_info=True)


@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: discord.app_commands.AppCommandError):
    """
    Global handler for slash command errors.
    """
    log.error(
        "App command error: cmd=%s user=%s guild=%s error=%r",
        getattr(interaction.command, "name", "Unknown"),
        interaction.user,
        interaction.guild,
        error,
    )

    if interaction.response.is_done():
        try:
            await interaction.followup.send(
                "❌ Something went wrong while running this command.",
                ephemeral=True,
            )
        except Exception:
            log.exception("Failed to send followup error message.")
    else:
        try:
            await interaction.response.send_message(
                "❌ Something went wrong while running this command.",
                ephemeral=True,
            )
        except Exception:
            log.exception("Failed to send initial error message.")


# ---------- Entry Point ----------
if __name__ == "__main__":
    log.info("Starting T0G Tournament Bot...")
    try:
        bot.run(TOKEN)
    except KeyboardInterrupt:
        log.warning("Bot interrupted with CTRL+C, shutting down...")
    except Exception as e:
        log.critical(f"Bot crashed with an unhandled exception: {e}", exc_info=True)