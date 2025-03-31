#!/usr/bin/oython
from .state import log, config
from discord.ext import commands
import discord
import traceback
import asyncio


log.set_log_level(config.log_level)

bot = commands.Bot(command_prefix=",", intents=discord.Intents.all())


async def run_bot():
    async with bot:
        # import cogs
        for m in config.modules:
            log.debug(f"Attempting to load extension {m}.")
            try:
                # this is a coroutine so we want to blocking wait it
                await bot.load_extension(f"extensions.{m}")
                # asyncio.run(bot.load_extension(f"extensions.{m}"))
            except commands.ExtensionNotFound:
                log.warn(f"Extension {m} was not found, skipping.")
            except commands.NoEntryPointError:
                log.warn(f"Extension {m} is missing a global setup function, skipping.")
            except commands.ExtensionFailed:
                log.warn(
                    f"Extension {m} failed somewhere in its setup process, skipping."
                )
                log.error(traceback.format_exc())
        log.info(f"Loaded {len(bot.cogs)} module(s).")

        @bot.event
        async def on_ready():
            log.info(f"Logged in to Discord as {bot.user}.")

        @bot.command(name="crash")
        async def crash(ctx: commands.Context[commands.Bot]):
            if ctx.author.id in config.admin_ids:
                await ctx.send("Crashing...")
                exit(1)
            else:
                await ctx.send("You are not an administrator.")

        await bot.start(config.bot_token)


if __name__ == "__main__":
    asyncio.run(run_bot())
