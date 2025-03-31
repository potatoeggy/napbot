import io
import time
import asyncio
from typing import TYPE_CHECKING

from .song import Song


from ...state import config, log
from ...utils import BotContext
import discord
from discord.ext import commands

if TYPE_CHECKING:
    from .voice import VoiceState

DEBUG_GUILDS = config.debug_guilds
MAX_LINES = 5


class MusicPanel(discord.ui.View):
    def __init__(
        self,
        bot: commands.Bot,
        title: str,
        voice_state: "VoiceState",
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
        voice_state: "VoiceState",
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
