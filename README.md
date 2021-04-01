# napbot
A Discord bot that tracks user reported sleep hours

It also carries some miscellaneous other functions such as LaTeX rendering and custom slash command registration.

The bot uses Discord's slash command integration for most of its features and the traditional bot prefix of `.` is deprecated.

### Features

 - Local music playing support with synchronised lyrics
 - Sleep tracking with various leaderboards
 - Custom slash command registration during runtime
 - Automatic LaTeX rendering with `$$...$$`

### Dependencies

 - [discord-py-slash-command](https://pypi.org/project/discord-py-slash-command/)
 - [discord.py](https://pypi.org/project/discord.py/)

### Configuration

The bot reads from a `config.json` in the current directory which can be changed with the `--config` flag. The following variables need to be set:

#### data_file: str

The file in which sleep data will be stored. Defaults to `"data.json"` in the current directory.

#### discord_id: int

The bot ID. Required.

#### discord_token: str

The bot token from Discord's developer portal. Required.

#### discord_guild: int

The server ID for Napbot to operate in. Required.

#### verbose: bool

Whether extended information should be sent to standard output. Defaults to `false`.

#### admin_user_id: int

The user ID that is permitted to crash the bot or override others' sleep hours. Defaults to None.

#### show_board_after_log: bool

Whether the leaderboard should be sent after each person logging their hours. Defaults to true.

#### walk_path: str

The path that the bot will search for music for. Defaults to `"/media/Moosic"`.

#### excluded_users: list

Users to not show on the leaderboard. Defaults to `[]`.

#### lyric_channel: int

The channel that synchronised lyrics will be sent to, or to disable lyrics if `0`. Defaults to `0`.
