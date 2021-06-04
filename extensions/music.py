from discord.ext import commands
from discord_slash import SlashCommand


class Music(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.log = bot.log
        self.root_path = (
            bot.config["music"].get("MusicPath", fallback="/media/Moosic")
            if "music" in bot.config
            else "/media/Moosic"
        )


def setup(bot: commands.Bot):
    bot.add_cog(Music(bot))
