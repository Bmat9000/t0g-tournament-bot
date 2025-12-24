# cogs/utils/discord_safe.py
# Discord API smoothness helpers:
# - safe_create_role
# - safe_create_text_channel
# - safe_delete_channel
# - safe_edit_message
#
# Goal:
# Prevent 429 rate limits + reduce "interaction failed" by pacing bulk operations.
#
# Works with discord.py 2.x

from __future__ import annotations

import asyncio
import random
from typing import Optional, Any

import discord


# ----------------------------
# pacing / retry core
# ----------------------------

async def _sleep_with_jitter(base: float, jitter: float = 0.15) -> None:
    await asyncio.sleep(max(0.0, base + random.uniform(0, jitter)))


async def _handle_rate_limit(exc: Exception, default_sleep: float = 1.5) -> float:
    """
    Returns suggested sleep time if exception looks like a Discord 429.
    discord.py often includes `retry_after` on HTTPException.
    """
    if isinstance(exc, discord.HTTPException):
        # Some versions expose .status
        status = getattr(exc, "status", None)
        if status == 429:
            retry_after = getattr(exc, "retry_after", None)
            if isinstance(retry_after, (int, float)) and retry_after > 0:
                return float(retry_after) + 0.2
            return default_sleep
    return 0.0


async def _retry_http(
    fn,
    *,
    tries: int = 5,
    base_sleep: float = 0.8,
    jitter: float = 0.2,
    allow_not_found: bool = False
):
    last_exc: Optional[Exception] = None
    for attempt in range(1, tries + 1):
        try:
            return await fn()
        except discord.NotFound:
            if allow_not_found:
                return None
            raise
        except (discord.Forbidden,) as e:
            # Permission issue: do not retry
            raise
        except discord.HTTPException as e:
            last_exc = e
            rl_sleep = await _handle_rate_limit(e)
            if rl_sleep > 0:
                await _sleep_with_jitter(rl_sleep, jitter=0.1)
            else:
                # other transient HTTP issues
                if attempt == tries:
                    break
                await _sleep_with_jitter(base_sleep * attempt, jitter=jitter)
        except Exception as e:
            last_exc = e
            if attempt == tries:
                break
            await _sleep_with_jitter(base_sleep * attempt, jitter=jitter)

    if last_exc:
        raise last_exc
    return None


# ----------------------------
# public helpers
# ----------------------------

async def safe_create_role(
    guild: discord.Guild,
    *,
    name: str,
    colour: Optional[discord.Colour] = None,
    hoist: bool = False,
    mentionable: bool = False,
    reason: Optional[str] = None,
    position_below: Optional[discord.Role] = None,
    spacing: float = 0.65,   # pacing between bulk creates
) -> discord.Role:
    """
    Creates a role with retry + pacing.
    If position_below is provided, attempts to position the role right below it.
    """
    async def _do_create():
        return await guild.create_role(
            name=name,
            colour=colour if colour is not None else discord.Colour.default(),
            hoist=hoist,
            mentionable=mentionable,
            reason=reason,
        )

    role: discord.Role = await _retry_http(_do_create)

    # Optional positioning (another API call; pace it)
    if position_below is not None:
        async def _do_edit():
            # place role right below position_below
            # discord positions: higher number = lower in list, but edit expects dict mapping role->position
            try:
                target_pos = max(1, position_below.position - 1)
            except Exception:
                target_pos = role.position
            return await role.edit(position=target_pos, reason=reason)

        try:
            await _sleep_with_jitter(0.35, jitter=0.1)
            await _retry_http(_do_edit, tries=3, base_sleep=0.6)
        except Exception:
            pass

    await _sleep_with_jitter(spacing, jitter=0.2)
    return role


async def safe_create_text_channel(
    guild: discord.Guild,
    *,
    name: str,
    category: Optional[discord.CategoryChannel] = None,
    overwrites: Optional[dict[discord.abc.Snowflake, discord.PermissionOverwrite]] = None,
    topic: Optional[str] = None,
    reason: Optional[str] = None,
    spacing: float = 0.65,
) -> discord.TextChannel:
    """
    Creates a text channel with retry + pacing.
    """
    async def _do_create():
        return await guild.create_text_channel(
            name=name,
            category=category,
            overwrites=overwrites,
            topic=topic,
            reason=reason,
        )

    ch: discord.TextChannel = await _retry_http(_do_create)
    await _sleep_with_jitter(spacing, jitter=0.2)
    return ch


async def safe_delete_channel(
    channel: discord.abc.GuildChannel,
    *,
    reason: Optional[str] = None,
    spacing: float = 0.55,
) -> None:
    """
    Deletes a channel with retry + pacing.
    allow_not_found=True so cleanup doesn't explode.
    """
    async def _do_delete():
        return await channel.delete(reason=reason)

    await _retry_http(_do_delete, tries=5, base_sleep=0.8, allow_not_found=True)
    await _sleep_with_jitter(spacing, jitter=0.2)


async def safe_edit_message(
    message: discord.Message,
    *,
    spacing: float = 0.25,
    **kwargs: Any
) -> Optional[discord.Message]:
    """
    Safe edit for frequently updated embeds (e.g., entry counts, panels).
    """
    async def _do_edit():
        return await message.edit(**kwargs)

    edited = await _retry_http(_do_edit, tries=5, base_sleep=0.6, allow_not_found=True)
    await _sleep_with_jitter(spacing, jitter=0.1)
    return edited


async def setup(bot):
    """discord.py extension entrypoint (no-op)."""
    return
