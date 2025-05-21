import os
import tempfile
from codecs import namereplace_errors
from importlib.metadata import always_iterable
from pathlib import Path
import traceback
import math
import random
import re
import asyncio
from warnings import catch_warnings

import yt_dlp
from typing import Literal, overload, Callable, Tuple

import spotipy
from spotipy import SpotifyOAuth, SpotifyClientCredentials
from yt_dlp.utils import whole_high

from .playlist import load_playlists

from ...utils import BotContext

from .song import SLUGIFY_PATTERN, Song, title_slugify, SongStatus, SpotifySong

from .voice import VoiceState
from ...state import config, log

import discord
from discord.ext import commands

from youtube_search import YoutubeSearch

MANUAL_LYRIC_OFFSET = 0
ITEMS_PER_PAGE = 10

SPOTIFY_MARKETS = ['AR', 'AU', 'AT', 'BE', 'BO', 'BR', 'BG', 'CA', 'CL', 'CO', 'CR', 'CY', 'CZ', 'DK', 'DO', 'DE', 'EC', 'EE', 'SV', 'FI', 'FR', 'GR', 'GT', 'HN', 'HK', 'HU', 'IS', 'IE', 'IT', 'LV', 'LT', 'LU', 'MY', 'MT', 'MX', 'NL', 'NZ', 'NI', 'NO', 'PA', 'PY', 'PE', 'PH', 'PL', 'PT', 'SG', 'SK', 'ES', 'SE', 'CH', 'TW', 'TR', 'UY', 'US', 'GB', 'AD', 'LI', 'MC', 'ID', 'JP', 'TH', 'VN', 'RO', 'IL', 'ZA', 'SA', 'AE', 'BH', 'QA', 'OM', 'KW', 'EG', 'MA', 'DZ', 'TN', 'LB', 'JO', 'PS', 'IN', 'BY', 'KZ', 'MD', 'UA', 'AL', 'BA', 'HR', 'ME', 'MK', 'RS', 'SI', 'KR', 'BD', 'PK', 'LK', 'GH', 'KE', 'NG', 'TZ', 'UG', 'AG', 'AM', 'BS', 'BB', 'BZ', 'BT', 'BW', 'BF', 'CV', 'CW', 'DM', 'FJ', 'GM', 'GE', 'GD', 'GW', 'GY', 'HT', 'JM', 'KI', 'LS', 'LR', 'MW', 'MV', 'ML', 'MH', 'FM', 'NA', 'NR', 'NE', 'PW', 'PG', 'WS', 'SM', 'ST', 'SN', 'SC', 'SL', 'SB', 'KN', 'LC', 'VC', 'SR', 'TL', 'TO', 'TT', 'TV', 'VU', 'AZ', 'BN', 'BI', 'KH', 'CM', 'TD', 'KM', 'GQ', 'SZ', 'GA', 'GN', 'KG', 'LA', 'MO', 'MR', 'MN', 'NP', 'RW', 'TG', 'UZ', 'ZW', 'BJ', 'MG', 'MU', 'MZ', 'AO', 'CI', 'DJ', 'ZM', 'CD', 'CG', 'IQ', 'LY', 'TJ', 'VE', 'ET', 'XK']

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

            self.guess_vote_skip_percent: float = (
                conf.getfloat("GuessVoteSkipPercent", 0.0) / 100
            )
            self.guess_lenient: bool = conf.getboolean("GuessLenient", fallback=True)
            self.temp_folder: Path = Path(tempfile.mkdtemp(dir=Path(conf.get("TempPath", fallback="/tmp"))))

        else:
            self.root_path = "/media/Moosic"
            self.show_song_status = False
            self.guess_vote_skip_percent = 0
            self.guess_lenient = False

        self.voice_state = VoiceState(
            self.bot, guess_vote_skip_percent=self.guess_vote_skip_percent
        )

        try:
            conf = config.config["spotify"]
            client_id=conf["ClientId"]
            client_secret=conf["ClientSecret"]
            if client_id is None or client_secret is None:
                log.info("Spotify not configured, skipping Spotify support.")
                bot.remove_command("spotifyadd")
            else:
                self.spotify_client = spotipy.Spotify(auth_manager=SpotifyClientCredentials(client_id=client_id, client_secret=client_secret))
        except Exception as e:
            log.error("Error authenticating with Spotify, skipping Spotify support.")
            log.debug(e)
            bot.remove_command("spotifyadd")
        # process all songs
        self.get_files()

    def get_files(self):
        self.songs: list[Song] = []

        log.info(f"Searching for songs from {self.root_path}.")
        ignored: int = 0
        for file in Path(self.root_path).rglob("*.mp3"):
            abs_path_parent = str(file.resolve().parent.absolute())
            abs_path = str(file.resolve().absolute())
            if file.suffix == ".mp3":
                for query in self.ignored_paths:
                    if query in abs_path_parent:
                        ignored += 1
                        break
                else:
                    try:
                        self.songs.append(Song(abs_path, log))
                    except IOError:
                        # expected if file not found
                        pass

        self.song_map = {song.path: song for song in self.songs}

        log.info(f"Found {len(self.songs)} songs, ignored {ignored}.")

        playlists = load_playlists()

        self.playlist_map: dict[str, list[Song]] = {}

        for name, songs in playlists.items():
            song_list: list[Song] = []
            for song in songs:
                if song in self.song_map:
                    song_list.append(self.song_map[song])
                else:
                    log.warn(
                        f"Playlist '{name}' contains song '{song}' which does not exist."
                    )
            self.playlist_map[name] = song_list

        log.info(f"Loaded {len(self.playlist_map)} playlists.")

    async def get_voice_state(self, ctx: BotContext):
        await self.voice_state.connect(ctx)

    def find_songs(self, query: str) -> list[Song]:
        if self.playlist_map.get(query):
            return self.playlist_map[query]

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
        start_pos: Literal["RANDOM", "CHORUS", "BEGINNING"] = "BEGINNING",
        pattern: str = "",
    ):
        if self.voice_state:  # if connected
            return await ctx.send(
                "Napbot must not be in a voice channel to turn on Guess Mode."
            )

        self.guess_leaderboard = dict[int, int]()
        self.voice_state.guess_mode = True
        self.voice_state.guess_show_artist = show_artist
        self.voice_state.start_pos = start_pos

        await self._play(ctx, pattern, 0, play_random=False, show_lyrics=False)
        await ctx.send("Guess mode activated! Type your guess of the song!")

    @commands.Cog.listener()
    async def on_message(self, msg: discord.Message):
        content = msg.content

        # ignore self
        if msg.author.id == self.bot.user.id or not (
            self.voice_state and self.voice_state.guess_mode
        ):
            return

        current_title = self.voice_state.current[0].title_slugified
        if title_slugify(content) == current_title or (
            self.guess_lenient and current_title in title_slugify(content)
        ):
            self.guess_leaderboard[msg.author.id] = (
                self.guess_leaderboard.get(msg.author.id, 0) + 1
            )
            await self.voice_state.skip()
            await msg.reply(
                f":white_check_mark: Correct, {msg.author}! Score: {self.guess_leaderboard[msg.author.id]}"
            )

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

        if not sources:
            await ctx.send(
                f"No songs matching '{query}' were found at the specified index."
            )
            return

        try:
            await self.get_voice_state(ctx)
        except AttributeError:
            print(traceback.format_exc())
            await ctx.send("You are not in a voice channel.")
            return
        except Exception as e:
            log.error(f"Error connecting to voice channel: {e}")
            await ctx.send("Error connecting to voice channel.")
            return

        if return_to_function:
            return sources

        await asyncio.wait_for(self.voice_state.ready.wait(), timeout=5)

        if not self.voice_state.audio_running:
            log.error("Audio player did not signal readiness, stopping playback.")
            return None

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
                f"Playing **{sources[0].get_name()}**, added {len(sources) - 1} songs to the queue."
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
                f"{offset + i + 1}. {n.get_name()}{' [LRC]' if n.lyrics else ''}\n"
            )
        embed.description += (
            f"\nPage {page + 1} of {math.ceil(len(sources) / ITEMS_PER_PAGE)}"
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
            match s[0].status:
                case SongStatus.NOT_FOUND:
                    status_icon = "❌"
                case SongStatus.NOT_AVAILABLE:
                    status_icon = "☁️"
                case SongStatus.DOWNLOADING:
                    status_icon = "⏳"
                case SongStatus.AVAILABLE:
                    status_icon = "✅"
                case SongStatus.LOCAL:
                    status_icon = "📀"

            embed.description += f"{offset + i + 1}. {s[0].get_name()}{' [LRC]' if s[0].lyrics else ''}{status_icon}\n"
        embed.description += f"\nPage {page + 1} of {math.ceil(len(self.voice_state.queue) / ITEMS_PER_PAGE)}"
        await ctx.send(embed=embed)

    @commands.command(name="playlists")
    async def show_playlists(self, ctx: BotContext, playlist: str = "", page: int = 1):
        if playlist:
            if playlist not in self.playlist_map:
                return await ctx.send(f"Playlist '{playlist}' not found.")
            songs = self.playlist_map[playlist]
            page -= 1
            if len(songs) < 1:
                return await ctx.send("Nothing in the playlist on this page.")
            offset = page * ITEMS_PER_PAGE
            embed = discord.Embed(title=f"Playlist '{playlist}'", description="")
            for i, s in enumerate(songs[offset : offset + ITEMS_PER_PAGE]):
                embed.description += (
                    f"{offset + i + 1}. {s.get_name()}{' [LRC]' if s.lyrics else ''}\n"
                )
            embed.description += (
                f"\nPage {page + 1} of {math.ceil(len(songs) / ITEMS_PER_PAGE)}"
            )
            await ctx.send(embed=embed)
            return

        embed = discord.Embed(title="Playlists", description="")
        for name, songs in self.playlist_map.items():
            embed.description += f"{name} ({len(songs)} songs)\n"
        await ctx.send(embed=embed)

    @commands.command(name="playlist")
    async def play_playlist(self, ctx: BotContext, name: str):
        if name not in self.playlist_map:
            return await ctx.send(f"Playlist '{name}' not found.")

        for song in self.playlist_map[name]:
            await self.voice_state.add(song)
        await ctx.send(
            f"Added {len(self.playlist_map[name])} songs from '{name}' to the queue."
        )

    @commands.command("spotifyadd")
    async def add_spotify(self, ctx: commands.Context, query: str = "", max_results: int = 50):
        try:
            await self.get_voice_state(ctx)
        except AttributeError:
            print(traceback.format_exc())
            await ctx.send("You are not in a voice channel.")
            return
        if not query:
            await ctx.send("Please provide a playlist name.")
            return
        if self.spotify_client is None:
            log.error("Spotify client not configured")
            await ctx.send("Spotify client not configured")
            return

        query_id: str = re.search(r'([A-Za-z0-9]{22})', query).group(1)
        await self._queue_spotify_internal(ctx, query_id, max_results)

    async def _queue_spotify_internal(self, ctx: commands.Context, query_id: str, max_results: int):
        spotify_tracks = []
        try:
            spotify_tracks.extend(
                i["track"] for i in (await self.bot.loop.run_in_executor(
                    None,
                    lambda: self.spotify_client.playlist_items(query_id, market=SPOTIFY_MARKETS, limit=max_results)
                ))["items"])
        except Exception as e:
            log.debug(f"Error fetching Spotify playlist: {e}")

        try:
            spotify_tracks.extend(
                i for i in (await self.bot.loop.run_in_executor(
                    None,
                    lambda: self.spotify_client.tracks([query_id], market=SPOTIFY_MARKETS)
                ))["tracks"] if i is not None)
        except Exception as e:
            log.debug(f"Error fetching Spotify tracks: {e}")

        try:
            spotify_tracks.extend((await self.bot.loop.run_in_executor(
                None,
                lambda: self.spotify_client.album_tracks(query_id, market=SPOTIFY_MARKETS, limit=max_results)
            ))["items"])
        except Exception as e:
            log.debug(f"Error fetching Spotify album: {e}")


        if len(spotify_tracks) == 0 :
            await ctx.send("Spotify playlist not found")
            return
        log.debug(f"Spotify playlist found: {spotify_tracks}")
        log.info(f"Found {len(spotify_tracks)} tracks in Spotify playlist")

        for track in spotify_tracks:
            if track is None:
                continue
            name = track["name"]
            artist = track["artists"][0]["name"]

            try:
                youtube_result = (await self.bot.loop.run_in_executor(None,
                    lambda: YoutubeSearch(f"{name} {artist}", max_results=1))).to_dict()
                log.debug(f"Youtube search result: {youtube_result}")

                external_id = youtube_result[0]["id"]

                log.info(f"Found video id {external_id} for {name} - {artist}")

                if (youtube_result is None
                    or len(youtube_result) == 0
                    or external_id is None):
                    log.warn(f"Youtube search failed for {name} - {artist}")
                    continue
            except Exception as e:
                log.error(f"Error searching for {name} - {artist}: {e}")
                continue

            song = SpotifySong(os.path.join(self.temp_folder, external_id + ".mp3"), log,
                               name, external_id, artist)

            await self.voice_state.add(song, False, False)

    def __del__(self):
        if self.temp_folder and self.temp_folder.exists():
            for file in self.temp_folder.iterdir():
                file.unlink()
            self.temp_folder.rmdir()
        log.debug("Deleted temp folder")

async def setup(bot: commands.Bot):
    await bot.add_cog(Music(bot))
