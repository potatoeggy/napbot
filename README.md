# napbot

A flexible and fully modular Discord bot with the following features:

## Local music

![image](https://user-images.githubusercontent.com/25178974/121067562-33ec9b00-c799-11eb-88cb-e9be77f40590.png)

For a given directory, Napbot can scan it for audio files (MP3s only for the time being) and display cover art and synchronised lyrics (if included as LRC). It supports typical music bot functions, such as:

- slash commands
- queuing and skipping tracks
- searching for tracks
- playing playlists

## Dependencies

- `discord.py`
- `discord-py-slash-command`

## Usage

```bash
uv sync --all-groups
uv run start
```
