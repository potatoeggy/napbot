import traceback
import math
import random
import re
import os
from typing import Literal, overload

from .discord import BotContext

from .song import SLUGIFY_PATTERN, Song

from .voice import VoiceState
from state import config, log

import discord
from discord.ext import commands

MANUAL_LYRIC_OFFSET = 0
ITEMS_PER_PAGE = 10


class Music(commands.Cog):
    def __init__(self, bot: commands.Bot):

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
