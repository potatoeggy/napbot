from discord.ext import commands
from discord_slash import SlashCommand
import eyed3
import os
import re

MANUAL_TIME_OFFSET = 0


class Song:
    def __init__(self, audio_path: str):
        self.base_name = os.path.splitext(os.path.basename(audio_path))[0]
        self.artist = None
        self.title = None
        self.album = None
        self.track_num = None
        self.art = None  # TODO: implement
        self.lyrics = []
        self.lyric_timestamps = []

        mp3 = eyed3.load(audio_path)
        if mp3 is not None:
            self.artist = mp3.tag.artist
            self.title = mp3.tag.title
            self.album = mp3.tag.album
            self.track_num = mp3.tag.track_num

        try:
            lrc_file = os.path.splitext(audio_path)[0] + ".lrc"
            with open(lrc_file, "r") as file:
                data = file.read().split("\n")
        except IOError:
            # file not found
            data = []

        for s in data:
            try:
                ts_end_index = s.index("]")
                ts = s[1:ts_end_index]
                ts_seconds = sum(
                    x * int(t)
                    for x, t in zip([0.001, 1, 60], reversed(re.split(":|\.", ts)))
                )
                lyric = s[ts_end_index + 1 :]
                if not lyric.isspace() and lyric != "":
                    self.lyrics.append(lyric)
                    self.lyric_timestamps.append(ts_seconds)
            except IndexError:
                # expected if newline or badly formatted LRC
                pass
            except ValueError:
                # current line does not have a timestamp
                pass


class Music(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.log = bot.log
        if "music" in bot.config.config:
            conf = bot.config.config["music"]
            self.root_path = conf.get("MusicPath", fallback="/media/Moosic")
            self.show_song_status = conf.getboolean(
                "CurrentSongAsStatus", fallback=False
            )
        else:
            self.root_path = "/media/Moosic"
            self.show_song_status = False

    def get_files(self):
        pass


def setup(bot: commands.Bot):
    bot.add_cog(Music(bot))
