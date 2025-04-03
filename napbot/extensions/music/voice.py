from collections import Counter
import random
import asyncio
from typing import Literal

import discord
from discord.ext import commands
from async_timeout import timeout

from .discord import LyricPlayer, MusicPanel
from ...utils import BotContext
from .song import Song, SongQueue


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
        self.start_pos: Literal["RANDOM", "CHORUS", "BEGINNING"] = "BEGINNING"

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
            if self.guess_mode:
                if self.start_pos == "RANDOM":
                    # pick a random lyric if any, otherwise fall back to beginning
                    first_third_timestamps = [
                        0,
                        *song.lyric_timestamps[: len(song.lyric_timestamps) // 3],
                    ]
                    start_time = random.choice(first_third_timestamps)
                elif self.start_pos == "CHORUS":
                    # attempt to find the chorus by finding the first most common
                    # lyric and its timestamp
                    # if no lyric timestamps, fall back to beginning
                    if song.lyric_timestamps:
                        most_common_lyric = Counter(song.lyrics).most_common(1)[0][0]
                        common_lyric_index = song.lyrics.index(most_common_lyric)
                        most_common_lyric_timestamp = song.lyric_timestamps[
                            common_lyric_index
                        ]
                        start_time = most_common_lyric_timestamp
                    else:
                        start_time = 0

            start_time_ms = int(
                (start_time % 1) * 1000
            )  # Convert fractional seconds to milliseconds
            start_time_s = int(start_time % 60)
            start_time_m = int((start_time // 60) % 60)
            start_time_h = int(start_time // 3600)
            start_ts = f"{start_time_h:02}:{start_time_m:02}:{start_time_s:02}.{start_time_ms:03}"
            if not self.vc:
                continue

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
