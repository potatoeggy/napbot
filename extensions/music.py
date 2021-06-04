from discord.ext import commands
from discord_slash import SlashCommand


class Music(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.log = bot.log
        if "music" in bot.config:
            conf = bot.config["music"]
            self.root_path = conf.get("MusicPath", fallback="/media/Moosic")
            self.show_song_status = conf.getboolean(
                "CurrentSongAsStatus", fallback=False
            )
        else:
            self.root_path = "/media/Moosic"
            self.show_song_status = False


def setup(bot: commands.Bot):
    bot.add_cog(Music(bot))
