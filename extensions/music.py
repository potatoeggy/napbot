from collections import deque
import string
import traceback
import io
import time
import math
import itertools
import random
import asyncio
import contextlib
import re
import os
from typing import Literal, overload

from collections.abc import Iterator

from ..iohandler import Logger
from state import config, log

import discord
from discord.ext import commands
from async_timeout import timeout

MANUAL_LYRIC_OFFSET = 0
ITEMS_PER_PAGE = 10
MAX_LINES = 5
DEBUG_GUILDS = config.debug_guilds
SLUGIFY_PATTERN = re.compile(rf"\s|\d|[{re.escape(string.punctuation)}]")

BotContext = commands.Context[commands.Bot]

try:
    from PIL import Image

    pillow_installed = True
except ImportError:
    log.warn("pillow is not installed, disabling dominant colour detection")
    pillow_installed = False

try:
    import eyed3

    eyed3_installed = True
except ImportError:
    log.warn("eyed3 is not installed, disabling metadata")
    eyed3_installed = False


def title_slugify(string: str) -> str:
    """
    Take the important part of a song title
    """
    par_index = string.find("(")

    new_index = par_index if par_index != -1 else None
    title_before_brackets = string[:new_index]
    title_slugified = re.sub(
        SLUGIFY_PATTERN, "", title_before_brackets.replace("&", "and")
    )
    return title_slugified.lower()


class Song:
    def __init__(self, audio_path: str, log: Logger):
        self.base_name = os.path.splitext(os.path.basename(audio_path))[0]
        self.path = audio_path
        self.path_lower = audio_path.lower()
        self.artist: str | None = None
        self.title: str | None = None
        self.album: str | None = None
        self.track_num: int | None = None
        self.art = None
        self.lyrics: list[str] = []
        self.lyric_timestamps: list[float] = []
        self.dominant_colour: discord.Color | None = None

        # get art
        if eyed3_installed:
            with open(os.devnull, "w") as null:
                with contextlib.redirect_stderr(null):
                    with contextlib.redirect_stdout(null):
                        mp3: eyed3.mp3.Mp3AudioFile = eyed3.load(audio_path)
            if mp3 is not None and mp3.tag is not None:
                self.artist = (
                    mp3.tag.artist.replace("\x00", ", ") if mp3.tag.artist else None
                )
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
                    for x, t in zip([0.001, 1, 60], reversed(re.split(r":|\.", ts)))
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

        self.title_slugified = (
            title_slugify(self.title) if self.title else self.base_name
        )

    def get_name(self):
        if not (self.title and self.artist):
            return self.base_name
        return f"{self.title} - {self.artist}"

    def __str__(self):
        if not (self.title and self.artist):
            return self.base_name
        return f"{self.title} - {self.artist}"


class SongQueue[T](asyncio.Queue[T]):
    _queue: deque[T]

    @overload
    def __getitem__(self, item: int) -> T: ...
    @overload
    def __getitem__(self, item: slice[T]) -> list[T]: ...
    def __getitem__(self, item: int | slice[T]) -> T | list[T]:
        if isinstance(item, slice):
            return list(itertools.islice(self._queue, item.start, item.stop, item.step))
        else:
            return self._queue[item]

    def __iter__(self) -> Iterator[T]:
        return self._queue.__iter__()

    def __len__(self):
        return self.qsize()

    def clear(self) -> None:
        self._queue.clear()

    def remove(self, index: int) -> None:
        del self._queue[index]

    def putfirst(self, item: T) -> None:
        self._queue.appendleft(item)


class VoiceState:
    def __init__(
        self,
        bot: commands.Bot,
        guess_mode: bool = False,
        guess_vote_skip_percent: float = 0.0,
    ):
        self.bot = bot
        self.queue = SongQueue[tuple[Song, bool]]()
        self.current = None
        self.loop = asyncio.get_event_loop()
        self.next = asyncio.Event()
        self.player = bot.loop.create_task(self.audio_player())
        self.vc: discord.VoiceClient | None = None
        self.audio_running = False

        self.guess_mode = guess_mode
        self.guess_show_artist = False
        self.guess_vote_skip_percent = guess_vote_skip_percent
        self.start_from_random_pos = False

    def __del__(self):
        self.player.cancel()

    def __bool__(self):
        return bool(self.vc)

    async def skip(self, num: int = 1) -> None:
        if not self.vc:
            return

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

    async def connect(self, ctx: BotContext):
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
        if not self.vc:
            return

        self.audio_running = True
        while True:
            try:
                async with timeout(180):
                    self.current = await self.queue.get()
            except asyncio.TimeoutError:
                self.bot.loop.create_task(self.stop())
                self.audio_running = False
                return

            song, show_lyrics = self.current
            start_time = 0
            if self.start_from_random_pos and self.guess_mode:
                # this only works if there are lyrics
                # start the time from anywhere
                first_third_timestamps = [
                    0,
                    *song.lyric_timestamps[: len(song.lyric_timestamps) // 3],
                ]
                start_time = random.choice(first_third_timestamps)

            start_time_ms = int(
                (start_time % 1) * 1000
            )  # Convert fractional seconds to milliseconds
            start_time_s = int(start_time % 60)
            start_time_m = int((start_time // 60) % 60)
            start_time_h = int(start_time // 3600)
            start_ts = f"{start_time_h:02}:{start_time_m:02}:{start_time_s:02}.{start_time_ms:03}"

            self.vc.play(
                discord.FFmpegOpusAudio(
                    source=song.path, bitrate=96, before_options=f"-ss {start_ts}"
                )
            )
            if not self.guess_mode:
                lyric_client = LyricPlayer(
                    self.vc, self.ctx, song, self, self.bot, show_lyrics
                )

                self.loop.create_task(lyric_client.start())

            if not self.guess_mode:
                await self.bot.change_presence(
                    activity=discord.Activity(
                        type=discord.ActivityType.listening,
                        name=song.get_name(),
                    )
                )

            if self.guess_mode:
                if self.guess_show_artist:
                    await self.ctx.send(
                        f"New song by **{song.artist}!**",
                        view=MusicPanel(
                            self.bot,
                            song.get_name(),
                            self,
                            guess_vote_skip_percent=self.guess_vote_skip_percent,
                        ),
                    )
                else:
                    await self.ctx.send(
                        "",
                        view=MusicPanel(
                            self.bot,
                            song.get_name(),
                            self,
                            guess_vote_skip_percent=self.guess_vote_skip_percent,
                        ),
                    )

            # launch monitor for guesses here
            while self.vc and self.vc.is_playing() and self.vc.is_connected():
                await asyncio.sleep(1)
            await self.bot.change_presence(activity=None)
            if self.guess_mode:
                await self.ctx.send(
                    f"That was **{song.get_name()}** ({song.title_slugified})!"
                )
            self.current = None

    async def stop(self):
        self.queue.clear()
        self.guess_mode = False
        await self.bot.change_presence(activity=None)
        if self.vc:
            self.vc.stop()
            await self.vc.disconnect()
            self.vc = None


class MusicPanel(discord.ui.View):
    def __init__(
        self,
        bot: commands.Bot,
        title: str,
        voice_state: VoiceState,
        *,
        guess_vote_skip_percent: float | None = None,
    ):
        super().__init__()
        self.users_to_ping = list(
            map(int, config.config["napbot"].get("AdminIds", "").split(","))
        )
        self.title = title
        self.bot = bot
        self.voice_state = voice_state
        self.guess_vote_skip_percent = guess_vote_skip_percent
        self.guess_vote_skips = set[int]()

    @discord.ui.button(emoji="⏭️", style=discord.ButtonStyle.primary)
    async def skip_track(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        if (
            self.voice_state.current
            and self.title == self.voice_state.current[0].get_name()
        ):
            if self.guess_vote_skip_percent is not None:
                members = self.voice_state.vc.channel.members
                num_members = len(members) - 1  # not itself

                if (
                    len(self.guess_vote_skips)
                    >= num_members * self.guess_vote_skip_percent
                ):
                    await self.voice_state.skip()
                elif interaction.user.id not in self.guess_vote_skips and any(
                    u.id == interaction.user.id for u in members
                ):
                    # if the user is in the voice channel and is not already in the list
                    self.guess_vote_skips.add(interaction.user.id)
                    if (
                        len(self.guess_vote_skips)
                        >= num_members * self.guess_vote_skip_percent
                    ):
                        await self.voice_state.skip()
                        button.disabled = True
                        button.style = discord.ButtonStyle.grey
                        button.emoji = "✅"
                button.label = f"{len(self.guess_vote_skips)}/{num_members}"
                return await interaction.response.edit_message(view=self)
            else:
                await self.voice_state.skip()
        button.disabled = True
        button.style = discord.ButtonStyle.grey
        button.emoji = "✅"
        await interaction.response.edit_message(view=self)

    @discord.ui.button(label="Request lyric fix", style=discord.ButtonStyle.green)
    async def ping_admin(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        for admin in self.users_to_ping:
            try:
                channel = await (await self.bot.fetch_user(admin)).create_dm()
                await channel.send(
                    f"**{str(interaction.user)}** would like you to update the lyrics for **{self.title}**."
                )
            except TypeError:
                log.error(f"{admin} is not a valid user id.")
        button.label = "Lyric fix requested"
        button.style = discord.ButtonStyle.grey
        button.disabled = True
        await interaction.response.edit_message(view=self)


class LyricPlayer:
    def __init__(
        self,
        vc: discord.VoiceClient,
        ctx: BotContext,
        source: Song,
        voice_state: VoiceState,
        bot: commands.Bot,
        show_lyrics: bool,
    ):
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
                    if not self.voice_state.current:
                        return  # usually because we skipped

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

        self.guess_mode = False

        # read configuration
        log.debug("Reading music configuration")
        if "music" in config.config:
            conf = config.config["music"]
            self.root_path: str = conf.get("MusicPath", fallback="/media/Moosic")
            self.show_song_status: bool = conf.getboolean(
                "CurrentSongAsStatus", fallback=False
            )
            ignored_paths = conf.get("IgnoredPaths", fallback="").split(",")
            self.ignored_paths: list[str] = (
                ignored_paths if ignored_paths[0] != "" else []
            )

            pillow_installed = conf.getboolean("DominantColorEmbed", pillow_installed)
            eyed3_installed = conf.getboolean("Id3Metadata", eyed3_installed)
            self.guess_vote_skip_percent: float | None = (
                conf.getfloat("GuessVoteSkipPercent", 0.0) / 100
            )
        else:
            self.root_path = "/media/Moosic"
            self.show_song_status = False
            self.guess_vote_skip_percent = None

        self.voice_state = VoiceState(
            self.bot, guess_vote_skip_percent=self.guess_vote_skip_percent
        )
        # process all songs
        self.get_files()

    def get_files(self):
        self.songs: list[Song] = []
        log.info(f"Searching for songs from {self.root_path}.")
        ignored: int = 0
        for root, _, files in os.walk(self.root_path):
            for name in files:
                if name.endswith(".mp3"):
                    for query in self.ignored_paths:
                        if query in root:
                            ignored += 1
                            break
                    else:
                        try:
                            self.songs.append(Song(os.path.join(root, name), log))
                        except IOError:
                            # expected if file not found
                            pass

        log.info(f"Found {len(self.songs)} songs, ignored {ignored}.")

    async def get_voice_state(self, ctx: BotContext):
        await self.voice_state.connect(ctx)

    def find_songs(self, query: str) -> list[Song]:
        args = [q for q in query.lower().split() if not q.startswith("-")]
        exclusion_terms = [q[1:] for q in query.lower().split() if q.startswith("-")]

        sources: list[Song] = []
        for song in self.songs:
            for q in exclusion_terms:
                if q in song.path_lower or q in song.get_name().lower():
                    break
            else:
                for q in args:
                    if not (q in song.path_lower or q in song.get_name().lower()):
                        break
                else:
                    sources.append(song)
        return sources

    @commands.command()
    async def guess(
        self,
        ctx: BotContext,
        show_artist: bool = False,
        start_from_random_pos: bool = False,
        pattern: str = "",
    ):
        if self.voice_state:  # if connected
            return await ctx.send(
                "Napbot must not be in a voice channel to turn on Guess Mode."
            )

        self.voice_state.guess_mode = True
        self.voice_state.guess_show_artist = show_artist
        self.voice_state.start_from_random_pos = start_from_random_pos

        await self._play(ctx, pattern, 0, play_random=False, show_lyrics=False)
        await ctx.send("Guess mode activated! Type your guess of the song!")

    @commands.Cog.listener()
    async def on_message(self, msg: discord.Message):
        content = msg.content

        if msg.author.bot or not (self.voice_state and self.voice_state.guess_mode):
            return

        current_title = self.voice_state.current[0].title_slugified
        if (
            re.sub(SLUGIFY_PATTERN, "", content.lower().replace("&", "and"))
            == current_title
        ):
            await self.voice_state.skip()
            await msg.reply(f":white_check_mark: Correct, {msg.author}!")

    @commands.command(name="play")
    async def play(
        self,
        ctx: BotContext,
        query: str = "",
        number: int = 1,
        play_random: bool = False,
        show_lyrics: bool = True,
    ):
        await self._play(ctx, query, number, play_random, show_lyrics)

    @overload
    async def _play(
        self,
        ctx: BotContext,
        query: str = "",
        number: int = 1,
        play_random: bool = False,
        show_lyrics: bool = True,
        return_to_function: Literal[True] = True,
    ) -> list[Song]: ...
    @overload
    async def _play(
        self,
        ctx: BotContext,
        query: str = "",
        number: int = 1,
        play_random: bool = False,
        show_lyrics: bool = True,
        return_to_function: Literal[False] = False,
    ) -> None: ...
    async def _play(
        self,
        ctx: BotContext,
        query: str = "",
        number: int = 1,
        play_random: bool = False,
        show_lyrics: bool = True,
        return_to_function: bool = False,
    ) -> list[Song] | None:
        if self.voice_state and self.voice_state.guess_mode:
            await ctx.send(
                "Cannot add songs while Guess Mode is on. "
                "Restore normal function by running /stop then /play."
            )
            return

        # treat numbers <= 0 as play all
        play_all = number <= 0
        if query and not play_random:
            # if there is a query
            try:
                sources = self.find_songs(query)
                if not play_all:
                    sources = [sources[number - 1]]
            except IndexError:
                await ctx.send(
                    f"No songs matching '{query}' were found at the specified index."
                )
                return
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
            await ctx.send("You are not in a voice channel.")
            return

        if return_to_function:
            return sources

        for s in sources:
            await self.voice_state.add(s, lyrics=show_lyrics)
        if len(sources) > 1:
            await ctx.send(f"Added {len(sources)} songs to the queue.")
        else:
            await ctx.send(f"Added **{sources[0].get_name()}** to the queue.")

    @commands.command(name="playnow")
    async def play_now(
        self,
        ctx: BotContext,
        query: str = "",
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

    @commands.command(name="playnext")
    async def play_next(
        self,
        ctx: BotContext,
        query: str = "",
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

    @commands.command(name="skip")
    async def skip(self, ctx: BotContext, number: int = 1):
        await self.voice_state.skip(number)
        await ctx.send("Skipped track.")

    @commands.command(name="search")
    async def search(self, ctx: BotContext, query: str, page: int = 1):
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

    @commands.command(name="stop")
    async def stop(self, ctx: BotContext):
        self.voice_state.guess_mode = False
        await self.voice_state.stop()
        await ctx.send("Goodbye!")

    @commands.command(name="clear")
    async def clear_queue(self, ctx: BotContext):
        self.voice_state.queue.clear()
        await ctx.send("Cleared the queue!")

    @commands.command(name="queue")
    async def show_queue(self, ctx: BotContext, page: int = 1):
        if self.voice_state and self.voice_state.guess_mode:
            return await ctx.send("Queue disabled in guess mode!")

        page -= 1
        if len(self.voice_state.queue) < 1:
            return await ctx.send("Nothing in the queue on this page.")
        offset = page * ITEMS_PER_PAGE
        embed = discord.Embed(title="Queue", description="")
        for i, s in enumerate(self.voice_state.queue[offset : offset + ITEMS_PER_PAGE]):
            embed.description += (
                f"{offset+i+1}. {s[0].get_name()}{' [LRC]' if s[0].lyrics else ''}\n"
            )
        embed.description += f"\nPage {page+1} of {math.ceil(len(self.voice_state.queue) / ITEMS_PER_PAGE)}"
        await ctx.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(Music(bot))
