from discord.ext import commands
from discord_slash import cog_ext, manage_commands
import eyed3
import os
import re
import contextlib
import asyncio
import random
import itertools
import discord
import math

MANUAL_LYRIC_OFFSET = 0
ITEMS_PER_PAGE = 10
DEBUG_GUILD = 812784271294726156


class Song:
    def __init__(self, audio_path: str):
        self.base_name = os.path.splitext(os.path.basename(audio_path))[0]
        self.path = audio_path
        self.path_lower = audio_path.lower()
        self.artist = None
        self.title = None
        self.album = None
        self.track_num = None
        self.art = None  # TODO: implement
        self.lyrics = []
        self.lyric_timestamps = []

        with open(os.devnull, "w") as null:
            with contextlib.redirect_stderr(null):
                mp3 = eyed3.load(audio_path)
        if mp3 is not None and mp3.tag is not None:
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
        except UnicodeDecodeError:
            # invalid LRC
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

    def get_name(self):
        if not (self.title and self.artist):
            return self.base_name
        return f"{self.title} - {self.artist}"


class SongQueue(asyncio.Queue):
    def __getitem__(self, item):
        if isinstance(item, slice):
            return list(itertools.islice(self._queue, item.start, item.stop, item.step))
        else:
            return self._queue[item]

    def __iter__(self):
        return self._queue.__iter__()

    def __len__(self):
        return self.qsize()

    def clear(self):
        self._queue.clear()

    def remove(self, index: int):
        del self._queue[index]


class VoiceState:
    # TODO: implement the event loop
    def __init__(self, ctx: commands.Context):
        # TODO: implement a queue here
        pass

    def skip(self, num: int = 1):
        pass

    def add(self, song: Song, right_away: bool = False, lyrics: bool = True):
        pass

    def remove(self, num: int):
        pass

    async def connect(self, ctx):
        channel = ctx.author.voice.channel
        self.vc = ctx.guild.voice_client
        if self.vc:
            if self.vc.channel.id == channel.id:
                self.vc.stop()
                return
            await self.vc.move_to(channel)
        else:
            await channel.connect()


class Music(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.log = bot.log
        self.voice_state = VoiceState(bot)

        # read configuration
        self.log.debug("Reading music configuration")
        if "music" in bot.config.config:
            conf = bot.config.config["music"]
            self.root_path = conf.get("MusicPath", fallback="/media/Moosic")
            self.show_song_status = conf.getboolean(
                "CurrentSongAsStatus", fallback=False
            )
        else:
            self.root_path = "/media/Moosic"
            self.show_song_status = False

        # process all songs
        self.get_files()

    def get_files(self):
        self.songs = []
        self.log.info(f"Searching for songs from {self.root_path}.")
        for root, _, files in os.walk(self.root_path):
            for name in files:
                if name.endswith(".mp3"):
                    try:
                        self.songs.append(Song(os.path.join(root, name)))
                    except IOError:
                        # expected if file not found
                        pass

        self.log.info(f"Found {len(self.songs)} songs.")

    async def get_voice_state(self, ctx):
        if self.voice_state:
            return self.voice_state
        self.voice_state = VoiceState(ctx)
        await self.voice_state.connect(ctx)

    async def find_songs(self, query) -> list:
        args = query.lower().split()
        sources = []
        for song in self.songs:
            for q in args:
                if not (q in song.path_lower or q in song.get_name().lower()):
                    break
            else:
                sources.append(song)
        return sources

    async def play(
        self,
        ctx,
        query="",
        number: int = 1,
        random: bool = False,
        show_lyrics: bool = True,
    ):
        # treat numbers <= 0 as play all
        play_all = number <= 0
        if not query and not random:
            # if there is a query
            try:
                sources = self.find_songs(query)
                if not play_all:
                    sources = [sources[number - 1]]
            except IndexError:
                return await ctx.send(
                    f"No songs matching '{query}' were found at the specified index."
                )
        else:
            # if query is empty play a random song
            if play_all:
                sources = self.songs.copy()
                random.shuffle(sources)
            else:
                sources = [random.choice(self.songs)]

        try:
            self.get_voice_state(ctx)
        except AttributeError:
            await ctx.send("You are not in a voice channel.")
            return

        # TODO: push to queue and report to user
        # Added 2 songs if more than 1, if 1 output song name

    async def play_now(self, ctx, query="", number: int = 1, show_lyrics: bool = True):
        pass

    async def play_next(self, ctx, query="", number: int = 1, show_lyrics: bool = True):
        pass

    @cog_ext.cog_slash(
        name="search",
        description="Searches local files for music",
        options=[
            manage_commands.create_option(
                name="query",
                description="Query to search for in tags",
                option_type=3,
                required=True,
            ),
            manage_commands.create_option(
                name="page",
                description="Page number to show",
                option_type=4,
                required=False,
            ),
        ],
        guild_ids=[DEBUG_GUILD],
    )
    async def search(self, ctx, query, page: int = 1):
        page -= 1
        sources = await self.find_songs(query)
        offset = page * ITEMS_PER_PAGE
        if len(sources) < offset:
            return await ctx.send(f"Page not found for query '{query}'.")

        embed = discord.Embed(title=f"Moosic containing '{query}'", description="")
        for i, n in enumerate(sources[offset : offset + ITEMS_PER_PAGE]):
            embed.description += f"{offset+i+1}. {n.get_name()}\n"
        embed.description += (
            f"\nPage {page+1} of {math.ceil(len(sources) / ITEMS_PER_PAGE)}"
        )
        await ctx.send(embed=embed)

    async def stop(self, ctx):
        pass

    async def clear_queue(self, ctx):
        pass

    async def show_queue(self, ctx):
        pass


def setup(bot: commands.Bot):
    bot.add_cog(Music(bot))
