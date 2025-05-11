from collections import deque
import io
import itertools
import asyncio
import contextlib
import re
import os
from typing import overload

from collections.abc import Iterator

from ...iohandler import Logger
from ...state import log, config

import discord
from opencc import OpenCC
import string


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


SLUGIFY_PATTERN = re.compile(rf"\s|\d|[{re.escape(string.punctuation)}]")

_non_ascii_punct_or_symbol = re.compile(r"[\p{P}\p{So}]+", flags=re.UNICODE)
_whitespace = re.compile(r"\s+")

cc = OpenCC("t2s.json")


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

    def clear(self) -> None:
        self._queue.clear()

    def remove(self, index: int) -> None:
        del self._queue[index]

    def putfirst(self, item: T) -> None:
        self._queue.appendleft(item)
