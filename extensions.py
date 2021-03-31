from discord.ext import commands
import discord

class archive_status(commands.Cog):
    def __init__(self, bot, embed_channel):
        self.bot = bot
        self.embed_channel = embed_channel
    
    @commands.Cog.listener()
    async def on_member_update(self, before, after):
        for a in after.activities:
            if a.type == discord.ActivityType.custom:
                if a.name == "": # if it was cleared
                    return
                for b in before.activities:
                    if b.type == discord.ActivityType.custom and b.name == a.name: # if custom was not the one changed
                        return
                emoji = "" if a.emoji is None else str(emoji)
                embed = discord.Embed(
                    description=f"{emoji} {a.name}"
                )
                embed.set_author(name=str(after), icon_url=after.avatar_url)
                await self.bot.get_channel(self.embed_channel).send(embed=embed)
            