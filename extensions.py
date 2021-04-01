from discord.ext import commands
import discord

class archive_status(commands.Cog):
    def __init__(self, bot, embed_channel):
        self.bot = bot
        self.embed_channel = embed_channel
    
    @commands.Cog.listener()
    async def on_member_update(self, before, after):
        if before.status == discord.Status.offline: # if status was not custom changed
            return
        a = str(next(filter(lambda i: i.type == discord.ActivityType.custom, after.activities), None)))
        b = str(next(filter(lambda i: i.type == discord.ActivityType.custom, before.activities), None)))
        if a == "" or b == a: # if it was cleared or if it hasn't changed
            return
        embed = discord.Embed(description=a)
        embed.set_author(name=str(after), icon_url=after.avatar_url)
        await self.bot.get_channel(self.embed_channel).send(embed=embed)
            
