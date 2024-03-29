#!/usr/bin/oython
import iohandler
from discord.ext import commands
import discord_slash
import discord
from discord_slash import SlashCommand
import traceback

log = iohandler.Logger()
config = iohandler.Config(log)
log.set_log_level(config.log_level)

if __name__ == "__main__":
    bot = commands.Bot(command_prefix=",")
    bot.log = log
    bot.config = config
    slash = SlashCommand(bot, sync_commands=True)
    # import cogs
    for m in config.modules:
        log.debug(f"Attempting to load extension {m}.")
        try:
            bot.load_extension(f"extensions.{m}")
        except commands.ExtensionNotFound:
            log.warn(f"Extension {m} was not found, skipping.")
        except commands.NoEntryPointError:
            log.warn(f"Extension {m} is missing a global setup function, skipping.")
        except commands.ExtensionFailed:
            log.warn(f"Extension {m} failed somewhere in its setup process, skipping.")
            log.error(traceback.format_exc())
    log.info(f"Loaded {len(bot.cogs)} module(s).")

    @bot.event
    async def on_ready():
        log.info(f"Logged in to Discord as {bot.user}.")

    @slash.slash(
        name="crash",
        description="Crash the bot",
        options=[],
        guild_ids=config.debug_guilds,
    )
    @bot.command(name="crash")
    async def crash(ctx):
        if ctx.author.id in config.admin_ids:
            await ctx.send("Crashing...")
            exit(1)
        else:
            await ctx.send("You are not an administrator.")

    bot.run(config.bot_token)
