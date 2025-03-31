from pathlib import Path
from typing import TYPE_CHECKING
from ...state import config, log

try:
    import m3u8

    playlists_enabled = config.config["music"].getboolean("Playlists", True)
except ImportError:
    log.warn("m3u8 is not installed, disabling playlist support")

    playlists_enabled = False

    if TYPE_CHECKING:
        import m3u8


def load_playlists() -> dict[str, list[str]]:
    if not playlists_enabled:
        return {}

    music_path = config.config["music"].get("MusicPath")

    if not music_path:
        return {}

    playlist_map: dict[str, list[str]] = {}

    for f in Path(music_path).rglob("*.m3u"):
        playlist = m3u8.load(str(f.resolve().absolute()))
        playlist_name = f.stem

        playlist_map[playlist_name] = [p.get_path_from_uri() for p in playlist.segments]
    return playlist_map
