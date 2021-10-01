MANUAL_LYRIC_OFFSET = 0
ITEMS_PER_PAGE = 10
MAX_LINES = 5
DEBUG_GUILDS = [812784271294726156]

from discord.ext import commands
from discord_slash import cog_ext, manage_commands
import os
import re
import contextlib
import asyncio
import random
import itertools
import discord
import math
from async_timeout import timeout
import time
import tempfile
import io
import traceback

try:
    from PIL import Image

    pillow_installed = True
except ImportError:
    print(" WARN: pillow is not installed, disabling dominant colour detection")
    pillow_installed = False

try:
    import eyed3

    eyed3_installed = True
except ImportError:
    print(
        " WARN: eyed3 is not installed, disabling metadata"
    )  # haha where logging module
    eyed3_installed = False


class Song:
    def __init__(self, audio_path: str, log):
        self.base_name = os.path.splitext(os.path.basename(audio_path))[0]
        self.path = audio_path
        self.path_lower = audio_path.lower()
        self.artist = None
        self.title = None
        self.album = None
        self.track_num = None
        self.art = None
        self.lyrics = []
        self.lyric_timestamps = []
        self.dominant_colour = None

        # get art
        if eyed3_installed:
            with open(os.devnull, "w") as null:
                with contextlib.redirect_stderr(null):
                    with contextlib.redirect_stdout(null):
                        mp3: eyed3.mp3.Mp3AudioFile = eyed3.load(audio_path)
            if mp3 is not None and mp3.tag is not None:
                self.artist = mp3.tag.artist
                self.title = mp3.tag.title
                self.album = mp3.tag.album
                self.track_num = mp3.tag.track_num
                art_frame: eyed3.id3.frames.ImageFrame = next(
                    (i for i in mp3.tag.images), None
                )

                if art_frame is not None:
                    self.art = art_frame.image_data
                    if pillow_installed:
                        with io.BytesIO(self.art) as imagedata:
                            image = (
                                Image.open(imagedata)
                                .convert("RGB")
                                .resize((1, 1), resample=0)
                            )
                            self.dominant_colour = discord.Colour.from_rgb(
                                *image.getpixel((0, 0))
                            )

        # parse lyrics
        try:
            lrc_file = os.path.splitext(audio_path)[0] + ".lrc"
            with open(lrc_file, "r") as file:
                data = file.read().split("\n")
        except IOError:
            # file not found
            data = []
        except UnicodeDecodeError:
            # invalid LRC
            log.warn(f"{self.get_name()}'s lyrics are not in UTF-8.")
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

    def __str__(self):
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
        # TODO: no idea why but the queue is just broken now

    def putfirst(self, item):
        self._queue.appendleft(item)


class VoiceState:
    # TODO: implement the event loop
    def __init__(self, bot: commands.Bot):
        # TODO: implement a queue here
        self.bot = bot
        self.queue = SongQueue()
        self.current = None
        self.loop = asyncio.get_event_loop()
        self.next = asyncio.Event()
        self.player = bot.loop.create_task(self.audio_player())
        self.vc = None
        self.audio_running = False

    def __del__(self):
        self.player.cancel()

    def __bool__(self):
        return self.vc

    async def skip(self, num: int = 1):
        num -= 1
        for _ in range(num):
            await self.queue.get()
        if self.current:
            self.vc.stop()

    async def add(self, song: Song, right_away: bool = False, lyrics: bool = True):
        if not right_away:
            await self.queue.put((song, lyrics))
        else:
            self.queue.putfirst((song, lyrics))

    def remove(self, num: int):
        self.queue.remove(num - 1)

    async def connect(self, ctx):
        channel = ctx.author.voice.channel
        self.vc = ctx.guild.voice_client
        if not self.audio_running:
            self.player = self.bot.loop.create_task(self.audio_player())
        if self.vc:
            if self.vc.channel.id == channel.id:
                return
            self.vc = await self.vc.move_to(channel)
        else:
            self.vc = await channel.connect()
        self.ctx = ctx

    async def audio_player(self):
        self.audio_running = True
        while True:
            try:
                async with timeout(180):
                    self.current = await self.queue.get()
            except asyncio.TimeoutError:
                self.bot.loop.create_task(self.stop())
                self.audio_running = False
                return
            lyric_client = LyricPlayer(
                self.vc, self.ctx, self.current[0], self, self.bot, self.current[1]
            )
            self.loop.create_task(lyric_client.start())
            self.vc.play(discord.FFmpegPCMAudio(source=self.current[0].path))
            await self.bot.change_presence(
                activity=discord.Activity(
                    type=discord.ActivityType.listening, name=self.current[0].get_name()
                )
            )
            while self.vc and self.vc.is_playing() and self.vc.is_connected():
                await asyncio.sleep(1)
            await self.bot.change_presence(activity=None)
            self.current = None

    async def stop(self):
        self.queue.clear()
        await self.bot.change_presence(activity=None)
        if self.vc:
            self.vc.stop()
            await self.vc.disconnect()
            self.vc = None


class MusicPanel(discord.ui.View):
    def __init__(self, bot: commands.Bot, title, voice_state: VoiceState):
        super().__init__()
        self.users_to_ping = list(
            map(int, bot.config.config["napbot"].get("AdminIds", "").split(","))
        )
        self.title = title
        self.bot = bot
        self.voice_state = voice_state

    @discord.ui.button(emoji="⏭️", style=discord.ButtonStyle.primary)
    async def skip_track(
        self, button: discord.ui.Button, interaction: discord.Interaction
    ):
        await self.voice_state.skip()
        button.disabled = True
        button.style = discord.ButtonStyle.grey
        button.emoji = "✅"
        await interaction.response.edit_message(view=self)

    @discord.ui.button(label="Request lyric fix", style=discord.ButtonStyle.green)
    async def ping_admin(
        self, button: discord.ui.Button, interaction: discord.Interaction
    ):
        for admin in self.users_to_ping:
            try:
                channel = await (await self.bot.fetch_user(admin)).create_dm()
                await channel.send(
                    f"**{str(interaction.user)}** would like you to update the lyrics for **{self.title}**."
                )
            except TypeError:
                self.bot.log.error(f"{admin} is not a valid user id.")
        button.label = "Lyric fix requested"
        button.style = discord.ButtonStyle.grey
        button.disabled = True
        await interaction.response.edit_message(view=self)


class LyricPlayer:
    def __init__(self, vc, ctx, source, voice_state, bot, show_lyrics):
        self.vc = vc
        self.ctx = ctx
        self.source = source
        self.voice_state = voice_state
        self.bot = bot
        self.show_lyrics = show_lyrics

    async def start(self):
        # grab file
        embed = discord.Embed(title=self.source.get_name(), description="")
        if self.source.title:
            embed.title = self.source.title
        if self.source.artist:
            embed.description += f"{self.source.artist}\n"
        if self.source.album:
            embed.description += f"{self.source.album}\n"
        # embed.description += f"{self.source.path}\n"
        if self.source.lyrics and self.show_lyrics:
            embed.add_field(
                name="Lyrics",
                value="\n".join(
                    self.source.lyrics[
                        : min(MAX_LINES * 2 + 1, len(self.source.lyrics))
                    ]
                ),
            )

        if self.source.art:
            with io.BytesIO(self.source.art) as imagedata:
                file = discord.File(fp=imagedata, filename="cover.jpg")
                embed.set_thumbnail(url="attachment://cover.jpg")
                if self.source.dominant_colour:
                    embed.color = self.source.dominant_colour
                msg = await self.ctx.channel.send(
                    embed=embed,
                    file=file,
                    view=MusicPanel(self.bot, self.source.get_name(), self.voice_state),
                )
        else:
            msg = await self.ctx.channel.send(
                embed=embed,
                view=MusicPanel(self.bot, self.source.get_name(), self.voice_state),
            )

        if self.show_lyrics:
            start = time.time()
            for i, t in enumerate(self.source.lyric_timestamps):
                now = time.time()
                lines_before = max(
                    0, min(i - MAX_LINES, len(self.source.lyrics) - MAX_LINES * 2)
                )
                lines_after = min(
                    len(self.source.lyrics),
                    max(i + MAX_LINES, MAX_LINES * 2 + 1 - lines_before),
                )
                embed.set_field_at(
                    0,
                    name="Lyrics",
                    value="\n".join(
                        self.source.lyrics[lines_before:i]
                        + [f"**{self.source.lyrics[i]}**"]
                        + (
                            self.source.lyrics[i + 1 : lines_after]
                            if i + 1 < len(self.source.lyrics)
                            else []
                        )
                    ),
                )
                while now < t + start:
                    if (
                        not self.voice_state.current[0].get_name()
                        == self.source.get_name()
                    ):
                        return
                    await asyncio.sleep(0.1)
                    now = time.time()
                await msg.edit(embed=embed)


class Music(commands.Cog):
    def __init__(self, bot: commands.Bot):
        global pillow_installed  # aiya
        global eyed3_installed  # aiya x2

        self.bot = bot
        self.log = bot.log
        self.voice_state = VoiceState(self.bot)

        # read configuration
        self.log.debug("Reading music configuration")
        if "music" in bot.config.config:
            conf = bot.config.config["music"]
            self.root_path = conf.get("MusicPath", fallback="/media/Moosic")
            self.show_song_status = conf.getboolean(
                "CurrentSongAsStatus", fallback=False
            )
            pillow_installed = conf.getboolean("DominantColorEmbed", pillow_installed)
            eyed3_installed = conf.getboolean("Id3Metadata", eyed3_installed)
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
                        self.songs.append(Song(os.path.join(root, name), self.log))
                    except IOError:
                        # expected if file not found
                        pass

        self.log.info(f"Found {len(self.songs)} songs.")

    async def get_voice_state(self, ctx):
        await self.voice_state.connect(ctx)

    def find_songs(self, query) -> list:
        args = query.lower().split()
        sources = []
        for song in self.songs:
            for q in args:
                if not (q in song.path_lower or q in song.get_name().lower()):
                    break
            else:
                sources.append(song)
        return sources

    @cog_ext.cog_slash(
        name="play",
        description="Play a moosic",
        options=[
            manage_commands.create_option(
                name="query",
                description="Tags to search for",
                option_type=3,
                required=False,
            ),
            manage_commands.create_option(
                name="number",
                description="Song number from search, or 0 for all songs that match search",
                option_type=4,
                required=False,
            ),
        ],
        guild_ids=DEBUG_GUILDS,
    )
    @commands.command()
    async def play(
        self,
        ctx,
        query="",
        number: int = 1,
        play_random: bool = False,
        show_lyrics: bool = True,
    ):
        await self._play(ctx, query, number, play_random, show_lyrics)

    async def _play(
        self,
        ctx,
        query="",
        number: int = 1,
        play_random: bool = False,
        show_lyrics: bool = True,
        return_to_function: bool = False,
    ) -> list:
        # treat numbers <= 0 as play all
        play_all = number <= 0
        if query and not play_random:
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
            else:
                sources = [random.choice(self.songs)]

        # if there's only one it doesn't matter if more we want to shuffle them
        random.shuffle(sources)

        try:
            await self.get_voice_state(ctx)
        except AttributeError:
            print(traceback.format_exc())
            return await ctx.send("You are not in a voice channel.")

        if return_to_function:
            return sources

        for s in sources:
            await self.voice_state.add(s, lyrics=show_lyrics)

        if len(sources) > 1:
            await ctx.send(f"Added {len(sources)} songs to the queue.")
        else:
            await ctx.send(f"Added **{sources[0].get_name()}** to the queue.")

    @cog_ext.cog_slash(
        name="playnow",
        description="Play a moosic now",
        options=[
            manage_commands.create_option(
                name="query",
                description="Tags to search for",
                option_type=3,
                required=False,
            ),
            manage_commands.create_option(
                name="number",
                description="Song number from search, or 0 for all songs that match search",
                option_type=4,
                required=False,
            ),
        ],
        guild_ids=DEBUG_GUILDS,
    )
    @commands.command(name="playnow")
    async def play_now(
        self,
        ctx,
        query="",
        number: int = 1,
        play_random: bool = False,
        show_lyrics: bool = True,
    ):
        sources = await self._play(
            ctx, query, number, play_random, show_lyrics, return_to_function=True
        )
        for s in sources:
            await self.voice_state.add(s, True, show_lyrics)
        if len(sources) > 1:
            await ctx.send(
                f"Playing **{sources[0].get_name()}**, added {len(sources)-1} songs to the queue."
            )
        else:
            await ctx.send(f"Playing **{sources[0].get_name()}**.")
        await self.voice_state.skip()

    @cog_ext.cog_slash(
        name="playnext",
        description="Play a moosic right after",
        options=[
            manage_commands.create_option(
                name="query",
                description="Tags to search for",
                option_type=3,
                required=False,
            ),
            manage_commands.create_option(
                name="number",
                description="Song number from search, or 0 for all songs that match search",
                option_type=4,
                required=False,
            ),
        ],
        guild_ids=DEBUG_GUILDS,
    )
    async def play_next(
        self,
        ctx,
        query="",
        number: int = 1,
        play_random: bool = False,
        show_lyrics: bool = True,
    ):
        sources = await self._play(
            ctx, query, number, play_random, show_lyrics, return_to_function=True
        )
        for s in sources:
            await self.voice_state.add(s, True, show_lyrics)
        if len(sources) > 1:
            await ctx.send(f"Added {len(sources)} songs to the queue.")
        else:
            await ctx.send(f"Added **{sources[0].get_name()}** to the queue.")

    @cog_ext.cog_slash(
        name="skip",
        description="Skip a number of tracks (default 1)",
        options=[
            manage_commands.create_option(
                name="number",
                description="The number of tracks to skip / the track number to skip to",
                option_type=4,
                required=False,
            )
        ],
        guild_ids=DEBUG_GUILDS,
    )
    async def skip(self, ctx, number: int = 1):
        await self.voice_state.skip(number)
        await ctx.send("Skipped track.")

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
        guild_ids=DEBUG_GUILDS,
    )
    async def search(self, ctx, query, page: int = 1):
        page -= 1
        sources = self.find_songs(query)
        offset = page * ITEMS_PER_PAGE
        if len(sources) < offset:
            return await ctx.send(f"Page not found for query '{query}'.")

        embed = discord.Embed(title=f"Moosic containing '{query}'", description="")
        for i, n in enumerate(sources[offset : offset + ITEMS_PER_PAGE]):
            embed.description += (
                f"{offset+i+1}. {n.get_name()}{' [LRC]' if n.lyrics else ''}\n"
            )
        embed.description += (
            f"\nPage {page+1} of {math.ceil(len(sources) / ITEMS_PER_PAGE)}"
        )
        await ctx.send(embed=embed)

    @cog_ext.cog_slash(
        name="stop",
        description="Stop playback and disconnect",
        options=[],
        guild_ids=DEBUG_GUILDS,
    )
    async def stop(self, ctx):
        await self.voice_state.stop()
        await ctx.send("Goodbye!")

    @cog_ext.cog_slash(
        name="clear", description="Clear the queue", options=[], guild_ids=DEBUG_GUILDS
    )
    async def clear_queue(self, ctx):
        self.voice_state.queue.clear()
        await ctx.send("Cleared the queue!")

    @cog_ext.cog_slash(
        name="queue",
        description="Show the queue",
        options=[
            manage_commands.create_option(
                name="page",
                description="The page number to view",
                option_type=4,
                required=False,
            )
        ],
        guild_ids=DEBUG_GUILDS,
    )
    async def show_queue(self, ctx, page: int = 1):
        page -= 1
        if len(self.voice_state.queue) < 1:
            return await ctx.send("Nothing in the queue on this page.")
        offset = page * ITEMS_PER_PAGE
        embed = discord.Embed(title=f"Queue", description="")
        for i, s in enumerate(self.voice_state.queue[offset : offset + ITEMS_PER_PAGE]):
            embed.description += (
                f"{offset+i+1}. {s[0].get_name()}{' [LRC]' if s[0].lyrics else ''}\n"
            )
        embed.description += f"\nPage {page+1} of {math.ceil(len(self.voice_state.queue) / ITEMS_PER_PAGE)}"
        await ctx.send(embed=embed)


def setup(bot: commands.Bot):
    bot.add_cog(Music(bot))
