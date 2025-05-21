import concurrent.futures
from asyncio import TaskGroup, Task
from collections import deque
import io
import itertools
import asyncio
import contextlib
import re
import os
from ctypes.wintypes import HTASK
from concurrent.futures import Future
from idlelib.pathbrowser import PathBrowser
from importlib.metadata import always_iterable
from operator import itemgetter
from types import new_class
from typing import overload, Callable, override, Tuple
from pathlib import Path
import yt_dlp

from collections.abc import Iterator

from ...iohandler import Logger
from ...state import log, config

import discord
from opencc import OpenCC
import regex
import string

from enum import Enum
try:
    import eyed3

    eyed3_installed = config.config["music"].getboolean("Id3Metadata", True)

except ImportError:
    log.warn("eyed3 is not installed, disabling metadata")
    eyed3_installed = False


try:
    from PIL import Image

    pillow_installed = config.config["music"].getboolean("DominantColorEmbed", True)
except ImportError:
    log.warn("pillow is not installed, disabling dominant colour detection")
    pillow_installed = False


SLUGIFY_PATTERN = regex.compile(rf"\s|\d|[{re.escape(string.punctuation)}]")

_non_ascii_punct_or_symbol = regex.compile(r"[\p{P}\p{So}]+", flags=re.UNICODE)
_whitespace = regex.compile(r"\s+")

cc = OpenCC("t2s.json")

class SongStatus(Enum):
    """
    Status of song's availability
    Resolved states LOCAL, AVAILABLE, NOT_FOUND have highest priority
    DOWNLOADING has second priority to ensure new downloads are not started with pending downloads
    NOT_AVAILABLE has lowest priority
    """
    LOCAL = 0
    AVAILABLE = 1
    NOT_FOUND = 2
    DOWNLOADING = 3
    NOT_AVAILABLE = 4


def title_slugify(title: str) -> str:
    """
    Slugify a song title with the rules:
      • Ignore anything after the first '('
      • Replace '&' with 'and'
      • Convert Traditional Chinese chars to Simplified
      • Strip emoji and non-ASCII punctuation/symbols
      • Collapse whitespace to single dashes, lowercase result
    """
    # 1. focus on text before a parenthetical
    cut = title.find("(")
    core = title[:cut] if cut != -1 else title

    # 2. minor substitutions
    core = core.replace("&", "and")

    # 3. Traditional‑>Simplified conversion
    core = cc.convert(core)

    # 4. drop emoji / non‑ASCII punctuation
    core = _non_ascii_punct_or_symbol.sub("", core)

    # 5. collapse whitespace and lower‑case
    core = _whitespace.sub("", core).lower()
    return core


class Song:
    song_count = 0
    def __init__(self, audio_path: str, log: Logger, status: SongStatus = SongStatus.LOCAL):
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
        self.status: SongStatus = status
        self.song_position = Song.song_count
        self.external_id = None
        self.log = log
        Song.song_count += 1

        if self.status == SongStatus.NOT_AVAILABLE:
            return
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
                    self.set_art(art_frame.image_data)

        # parse lyrics
        self.get_subtitles()

        self.title_slugified = (
            title_slugify(self.title) if self.title else self.base_name
        )

    def get_subtitles(self):
        try:
            lrc_file = os.path.splitext(self.path)[0] + ".lrc"
            with open(lrc_file, "r") as file:
                data = file.read().split("\n")
        except IOError:
            # file not found
            data = []
        except UnicodeDecodeError:
            # invalid LRC
            self.log.warn(f"{self.get_name()}'s lyrics are not in UTF-8.")
            data = []
        for s in data:
            try:
                ts_end_index = s.index("]")
                ts = s[1:ts_end_index]
                ts_seconds = sum(
                    x * int(t)
                    for x, t in zip([0.001, 1, 60], reversed(re.split(r":|\.", ts)))
                )
                lyric = s[ts_end_index + 1:]
                if not lyric.isspace() and lyric != "":
                    self.lyrics.append(lyric)
                    self.lyric_timestamps.append(ts_seconds)
            except IndexError:
                # expected if newline or badly formatted LRC
                pass
            except ValueError:
                # current line does not have a timestamp
                pass

    def set_art(self, image_data: bytes):
        self.art = image_data
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

    def get_name(self):
        if not (self.title and self.artist):
            return self.base_name
        return f"{self.title} - {self.artist}"

    def __str__(self):
        if not (self.title and self.artist):
            return self.base_name
        return f"{self.title} - {self.artist}"

    def __lt__(self, other):
        if not isinstance(other, Song):
            return NotImplemented

        if self.status != other.status and self.status.value >= SongStatus.DOWNLOADING.value:
            return self.status.value < other.status.value
        return self.song_position < other.song_position # other statuses compare by position

    def download_track(self, lyric: bool, queue: 'SongQueue'):
        pass

class SpotifySong(Song):

    def __init__(self, audio_path: str, log: Logger, title: str, external_id: str, artist: str):
        super().__init__(audio_path, log, SongStatus.NOT_AVAILABLE)
        self.title = title
        self.title_slugified = title_slugify(title)
        self.external_id = external_id
        self.artist = artist


    @override
    def download_track(self, lyric: bool, queue: 'SongQueue'):
        song_tuple = (self, lyric)
        async def _reinsert_queue(song_tuple: Tuple['Song', bool]):
            for (i, item) in enumerate(queue):
                if item == song_tuple:
                    break
            await queue.reinsert(song_tuple)

        reinsert_queue = lambda: asyncio.run_coroutine_threadsafe(_reinsert_queue(song_tuple), queue.loop)

        path = Path(self.path)
        folder = path.parent
        thumbnail_path = path.with_suffix(".jpg")

        if path.exists():
            log.info(f"File already exists for {self}")
            self.status = SongStatus.AVAILABLE

            try:
                with Image.open(thumbnail_path) as image, io.BytesIO() as output:
                    image.convert("RGB").save(output, format="JPEG")
                    self.set_art(output.getvalue())

                    self.get_subtitles()
            except Exception as error:
                log.warn(f"Error setting thumbnail {thumbnail_path} and subtitles, with {error}")


            reinsert_queue()
            return

        try:
            current_downloaded = [f for f in os.listdir(folder) if f.endswith(".mp3")]
            if len(current_downloaded) >= config.config["music"].getint("MaxDownloadQueue", 5) * 2:
                current_downloaded.sort(key=lambda x: os.path.getctime(folder / x))
                for oldest_song in current_downloaded: # remove the oldest song if not in queue until under twice the download limit
                    if not queue or all(item[0] != oldest_song for item in queue) or queue.last_item != oldest_song:
                        try:
                            os.remove(folder / oldest_song)
                            if path.with_suffix(".jpg").exists():
                                os.remove(folder / Path(oldest_song).with_suffix(".jpg"))
                            if path.with_suffix(".lrc").exists():
                                os.remove(folder / Path(oldest_song).with_suffix(".lrc"))

                            log.info(f"Removed oldest song {oldest_song} from cache")
                        except PermissionError as remove_error:
                            log.warn(f"Error removing {oldest_song}, in use: {remove_error}")

                        current_downloaded = [f for f in os.listdir(folder) if f.endswith(".mp3")]
                        if len(current_downloaded) < config.config["music"].getint("MaxDownloadQueue", 5) * 2:
                            break

                if len(current_downloaded) >= config.config["music"].getint("MaxDownloadQueue", 5) * 2:
                    log.info(f"Cache is full, wait for other downloads before starting download for {self}")
                    self.status = SongStatus.NOT_AVAILABLE
                    return
        except Exception as cacheError:
            log.error(f"Error pruning cache: {cacheError}")
            self.status = SongStatus.NOT_AVAILABLE
            return

        log.info(f"Downloading song {self}")

        yt_opts = {'extract_flat': 'discard_in_playlist',
                   'final_ext': 'mp3',
                   'format': 'bestaudio/best',
                   'fragment_retries': 10,
                   'ignoreerrors': 'only_download',
                   'noplaylist': True,
                   'outtmpl': {'default': f'{folder}/%(id)s.%(ext)s'},
                   'postprocessors': [{'format': 'lrc',
                                       'key': 'FFmpegSubtitlesConvertor',
                                       'when': 'before_dl'},
                                      {'format': 'jpg',
                                       'key': 'FFmpegThumbnailsConvertor',
                                       'when': 'before_dl'},
                                      {'key': 'FFmpegExtractAudio',
                                       'nopostoverwrites': False,
                                       'preferredcodec': 'mp3',
                                       'preferredquality': '5'},
                                      {'key': 'FFmpegConcat',
                                       'only_multi_video': True,
                                       'when': 'playlist'}],
                   'writesubtitles': True,
                   'subtitleslangs': ['.*orig', 'en'],
                   'writethumbnail': True}
        log.debug(f"Youtube options: {yt_opts}")
        status_code = 1
        try:
            with yt_dlp.YoutubeDL(yt_opts) as ydl:
                status_code = ydl.download(self.external_id)
        except Exception as e:
            log.error(f"Error downloading with exception {e}")
            status_code = 1

        if status_code > 0:
            log.warn(f"Youtube download failed for {self}")
        else:
            log.info(f"Downloaded to {path} for {self}")


        try:
            with Image.open(thumbnail_path) as image, io.BytesIO() as output:
                image.convert("RGB").save(output, format="JPEG")
                self.set_art(output.getvalue())

                self.get_subtitles()
        except Exception as error:
            log.warn(f"Error setting thumbnail {thumbnail_path} and subtitles, with {error}")

        self.status = SongStatus.AVAILABLE if status_code == 0 else SongStatus.NOT_FOUND
        reinsert_queue()

class SongQueue[T](asyncio.PriorityQueue[T]):
    _queue: deque[T]
    _maxDownloadSize = config.config["music"].getint("MaxDownloadQueue", 5)
    _download_executor = concurrent.futures.ThreadPoolExecutor(
        max_workers=_maxDownloadSize,
        thread_name_prefix="Downloader"
    )
    _wake_event = asyncio.Event()
    last_item: T | None = None

    @overload
    def __init__(self, loop: asyncio.AbstractEventLoop, maxsize: int = 0) -> None: ...
    def __init__(self, loop: asyncio.AbstractEventLoop, maxsize: int = 0) -> None:
        super().__init__(maxsize)
        self.loop = loop

    @overload
    def __getitem__(self, item: int) -> T: ...
    @overload
    def __getitem__(self, item: slice) -> list[T]: ...
    def __getitem__(self, item: int | slice) -> T | list[T]:
        if isinstance(item, slice):
            return list(itertools.islice(self._queue, item.start, item.stop, item.step))
        else:
            return self._queue[item]

    def __iter__(self) -> Iterator[T]:
        return self._queue.__iter__()

    def __len__(self):
        return self.qsize()

    def _onUpdate(self) -> Future | None:
        if not self._queue or not isinstance(self._queue[0], tuple):
            return None
        no_available_songs = True
        last_task = None
        try:
            for song, lyric in list(self._queue)[:self._maxDownloadSize]:
                if isinstance(song, Song) and song.status == SongStatus.NOT_AVAILABLE:
                    song.status = SongStatus.DOWNLOADING
                    last_task = self._download_executor.submit(song.download_track, lyric, self)
                elif isinstance(song, Song) and (song.status == SongStatus.AVAILABLE or song.status == SongStatus.LOCAL):
                    self._wake_event.set()
                    no_available_songs = False

            if no_available_songs:
                self._wake_event.clear()
        except Exception as e:
            log.error(f"Error in _onUpdate: {e}")
            return None

        return last_task

    @override
    def put_nowait(self, item: T) -> None:
        try:
            super().put_nowait(item)
        except Exception as e:
            log.error(f"Error in put_nowait: {e}")
            return
        self._onUpdate()

    async def get_with_update(self) -> T:
        await self._wake_event.wait() # wait for a song with status AVAILABLE or LOCAL
        item = await self.get()
        self.last_item = item
        self._onUpdate()
        return item

    def clear(self) -> None:
        self._queue.clear()

    async def reinsert(self, item: T) -> None:
        self.remove(item)
        await self.put(item)

    def remove(self, index: int) -> None:
        del self._queue[index]

    def remove_with_update(self, index: int) -> None:
        self.remove(index)
        self._onUpdate()

    def putfirst(self, item: T) -> None:
        task = self._onUpdate() # TODO: original implementation of putfirst doesnt work - it won't wake waiting getters
        if task is not None: # only add the song if the task is finished downloading
            task.add_done_callback(lambda _: self._queue.appendleft(item))
        else:
            self._queue.appendleft(item)

