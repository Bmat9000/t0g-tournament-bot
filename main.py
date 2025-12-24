"""Bot entrypoint.

Cleanup Roadmap v1:
- Step 2: Centralize config & paths (core.config)
- Step 3/4: Centralize logging (core.logging_setup)

Behavior: unchanged.
"""

import discord
from discord.ext import commands

from core.config import ROOT, env  # loads .env once
from core.logging_setup import setup_logging

log = setup_logging("T0G_Tournament_Bot")

# ---------- Env & Token ----------
TOKEN = env("DISCORD_TOKEN")
if not TOKEN:
    log.critical("DISCORD_TOKEN not found in .env file – cannot start bot.")
    raise RuntimeError("DISCORD_TOKEN not found in .env file")

# ---------- Intents ----------
intents = discord.Intents.default()
intents.members = True
intents.guilds = True

# ---------- Paths ----------
COGS_DIR = ROOT / "cogs"


class T0GTournamentBot(commands.Bot):
    def __init__(self):
        super().__init__(
            command_prefix="!",
            intents=intents,
            application_id=None,
        )
        log.info("Bot instance created.")

    async def setup_hook(self):
        log.info("Running setup_hook...")

        if not COGS_DIR.exists():
            log.warning("Cogs directory %s does not exist.", COGS_DIR.resolve())
        else:
            log.info("Searching for cogs in %s ...", COGS_DIR.resolve())

        # Auto-discover and load all .py files directly inside /cogs
        for path in COGS_DIR.glob("*.py"):
            if path.name.startswith("_"):
                continue

            ext_name = f"cogs.{path.stem}"
            try:
                await self.load_extension(ext_name)
                log.info("✅ Loaded cog: %s", ext_name)
            except Exception as e:
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
        log.info("Logged in as %s (ID: %s)", self.user, self.user.id)
        log.info("T0G Tournament Bot is online and ready.")
        await self.change_presence(
            activity=discord.Game(name="T0G Tournaments | /create_tournament")
        )


bot = T0GTournamentBot()


@bot.event
async def on_error(event_method, *args, **kwargs):
    log.exception(f"Unhandled error in event '{event_method}'", exc_info=True)


@bot.tree.error
async def on_app_command_error(
    interaction: discord.Interaction,
    error: discord.app_commands.AppCommandError,
):
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


if __name__ == "__main__":
    log.info("Starting T0G Tournament Bot...")
    try:
        bot.run(TOKEN)
    except KeyboardInterrupt:
        log.warning("Bot interrupted with CTRL+C, shutting down...")
    except Exception as e:
        log.critical(f"Bot crashed with an unhandled exception: {e}", exc_info=True)
