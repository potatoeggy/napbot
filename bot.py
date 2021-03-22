#!/usr/bin/python

import os
import re
import sys
import json
import math
import threading
import discord
import discord_slash
from discord.ext import commands, tasks
from discord_slash import SlashCommand, cog_ext
from discord_slash.utils import manage_commands

import traceback
import asyncio
import requests
import time
import datetime
import random

VERBOSE = True
# TODO: add ping reminder at noon for those who have not submitted to submit


def debug(string, urgent=False):
	if urgent:
		print(string)
	elif VERBOSE:
		print("DEBUG:", string)


def show_help():  # TODO: consider adding
	debug("Exiting for showing help", urgent=True)
	exit(0)


def today():
	return datetime.date.today()


def last_saturday():
	offset = datetime.timedelta((today().weekday()+1) % 7 + 1)
	if today().weekday() == 5:
		offset = datetime.timedelta(days=0)
	return today() - offset


def cumulative(discord_id: int, data: dict):
	return sum(data[str(discord_id)].values())


def cumulative_average(discord_id: int, data: dict):
	return round(cumulative(discord_id, data) / len(data[str(discord_id)]), 2)


def cumulative_month(discord_id: int, data: dict):
	return sum(dict(filter(lambda p: datetime.date.fromisoformat(p[0]).month == today().month and datetime.date.fromisoformat(p[0]).year == today().year, data[str(discord_id)].items())).values())


def cumulative_week(discord_id: int, data: dict):
	return sum(dict(filter(lambda p: datetime.date.fromisoformat(p[0]) >= last_saturday(), data[str(discord_id)].items())).values())


def hours_today(discord_id: int, data: dict):
	return data[str(discord_id)][str(today())] if str(today()) in data[str(discord_id)] else 0


def push_data(discord_id: int, hours: int, data, data_file):  # TODO: add previous day support
	discord_id = str(discord_id)
	data[discord_id][str(today())] = hours
	with open(data_file, "w") as file:
		file.write(json.dumps(data, indent=4))


def init():
	# TODO: import from env vars
	# load conf file
	debug("Reading configuration...", urgent=True)
	config_file = os.path.join(sys.path[0], "config.json")
	try:
		config_file = sys.argv[sys.argv.index("--config") + 1]
	except ValueError:
		pass  # user did not specify custom conf location
	except IndexError:
		show_help()

	jsondict = {}
	try:
		with open(config_file, "r") as file:
			data = file.read()
		jsondict = json.loads(data)
	except EnvironmentError:
		# config file is probably not found and so will use fallback
		debug("Could not find config file.")

	def check_config(key, fallback=None):
		result = jsondict[key] if key in jsondict else fallback
		args_key = "--" + key.replace("_", "-")
		try:
			if type(fallback) is not bool:
				result = type(fallback)(sys.argv[sys.argv.index(args_key) + 1])
			elif args_key in sys.argv:
				result = True
		except ValueError:
			pass  # user did not specify conf in command line
		except IndexError:
			show_help()
		return result

	# mandatory fields
	discord_id = check_config("discord_id")
	discord_token = check_config("discord_token")
	discord_guild = check_config("discord_guild")
	admin_user_id = check_config("admin_user_id")
	show_board_after_log = check_config("show_board_after_log", True)
	walk_path = check_config("walk_path", "/media/Moosic/")
	VERBOSE = check_config("verbose", False)
	excluded_users = check_config("excluded_users", [])
	lyric_channel = check_config("lyric_channel", 0)

	# load saved data
	debug("Reading data...")
	data_file = check_config(
		"data_file", os.path.join(sys.path[0], "data.json"))
	data = {}
	try:
		with open(data_file, "r") as file:
			data = file.read()
		data = json.loads(data)
		for user in excluded_users:
			if data.pop(str(user), None) is None:
				debug(f"User {user} not found in data file and is excluded.")
	except EnvironmentError:
		# data file is probably not found and so will crash
		debug("Could not find data file. Exiting...", urgent=True)
		exit()
	
	moosics = {}
	for root, dirs, files in os.walk(walk_path):
			for name in files:
				if name.endswith(".mp3"):
					moosics[name.lower()] = (name, os.path.join(root, name))

	return discord_guild, discord_token, data, data_file, admin_user_id, show_board_after_log, moosics, lyric_channel

class LyricPlayer():
	__slots__ = ["vc", "filename", "channel", "lyrics", "running"]
	def __init__(self, vc, filename, channel):
		self.vc = vc
		self.filename = filename
		self.channel = channel
		self.running = False
		try:
			with open(filename, "r") as file:
				data = file.read().split("\n")
		except IOError:
			self.channel = 0
		
		self.lyrics = []
		if self.channel == 0: return
		time_delta = 0
		for s in data:
			try:
				end_stamp = s.index("]")
				time_string = s[1:end_stamp]
				time_string_ms = sum(x * int(t) for x, t in zip([0.001, 1, 60], reversed(re.split(":|\.", time_string))))-0.020
				lyric_line = s[end_stamp + 1:]
				if not lyric_line.isspace() and lyric_line != "":
					self.lyrics.append((time_string_ms - time_delta, lyric_line))
					time_delta = time_string_ms
			except IndexError:
				pass # expected if newline or badly formatted LRC
			except ValueError:
				pass # line does not have stamp
	
	async def start(self):
		self.running = True
		if self.channel == 0: return
		while not self.running:
			await asyncio.sleep(0.1)

		for t, s in self.lyrics:
			await asyncio.sleep(t)
			await self.channel.send(f"🎵 {s}")
			if not self.running:
				break

	def stop(self):
		self.running = False


if __name__ == "__main__":
	guild_id, client_token, data, data_file, admin_user_id, show_board_after_log, moosics, lyric_channel = init()
	command_prefix = "."
	bot = commands.Bot(command_prefix=command_prefix,
					   intents=discord.Intents.all())
	slash = SlashCommand(bot, sync_commands=True)
	command_register = []

	@bot.event
	async def on_ready():
		guild = discord.utils.get(bot.guilds, id=guild_id)
		debug(
			f"{bot.user} connected to Discord to {guild} (id: {guild_id}).", urgent=True)

	@slash.slash(
		name="slept",
		description="Records the number of hours you slept last night",
		options=[
			manage_commands.create_option(
					name="hours_slept",
					description="Number of hours slept last night",
					option_type=4,
					required=True
			),
			manage_commands.create_option(
				name="user_override",
				description="The user to update hours for",
				option_type=6,
				required=False,
			)
		],
		guild_ids=[guild_id]
	)
	@bot.command(name="slept", help="Records the number of hours you slept last night", aliases=["islept", "s"])
	async def save_hours(ctx, hours_slept: int, user_override: discord.Member = None):
		sender = user_override.id if user_override != None else ctx.author.id
		if user_override != None and ctx.author.id != admin_user_id:
			await ctx.send(f"ERROR: {ctx.author} does not have override permissions.")
			return
		if not 0 <= hours_slept <= 11:
			await ctx.send(f"ERROR: {hours_slept} hours is not in the range of 0 to 11 hours.")
			return

		push_data(sender, hours_slept, data, data_file)
		await leaderboard(ctx, show_board=show_board_after_log)

	@slash.slash(
		name="stats",
		description="Shows sleep statistics (you by default)",
		options=[
			manage_commands.create_option(
					name="target_id",
					description="The user to get statistics for instead",
					option_type=6,
					required=False,
			)
		],
		guild_ids=[guild_id]
	)
	@bot.command(name="stats", help="Shows sleep statistics (you by default)", aliases=["me"])
	async def stats(ctx, user: discord.Member = None):
		sender = ctx.author if user is None else user
		embed = discord.Embed(title=f"Sleep statistics for {sender.name}:")
		embed.add_field(name="Cumulative hours slept:",
						value=cumulative(sender.id, data), inline=False)
		embed.add_field(name="Average of cumulative hours slept:",
						value=cumulative_average(sender.id, data), inline=False)
		embed.add_field(name="Hours slept this month:",
						value=cumulative_month(sender.id, data), inline=False)
		embed.add_field(name="Hours slept this week:",
						value=cumulative_week(sender.id, data), inline=False)
		embed.add_field(name="Hours slept last night:",
						value=hours_today(sender.id, data), inline=False)
		await ctx.send(embed=embed)

	@slash.slash(
		name="board",
		description="Show everyone's sleep stats",
		options=[
			manage_commands.create_option(
					name="board_type",
					description="Type of statistic to get",
					option_type=3,
					required=False,
					choices=["weekly"]
			)
		],
		guild_ids=[guild_id]
	)
	@bot.command(name="leaderboard", help="Show everyone's sleep stats", aliases=["board"])
	async def board(ctx, board_type="weekly", show_board=True):
		await leaderboard(ctx, board_type, show_board)

	async def leaderboard(ctx, board_type="weekly", show_board=True):  # TODO: implement
		is_time_for_end_prize = False
		is_time_for_end_prize = today().weekday() == 4 and all(
			str(today()) in i[1] for i in data.items())
		if not show_board and not is_time_for_end_prize:
			return

		# TODO: implement non-weekly leaderboards
		weekly = []
		days_remaining = 7-(today()-last_saturday()).days
		for i, d in data.items():
			weekly.append((cumulative_week(str(i), data), i))
		weekly.sort(reverse=True)
		embed = discord.Embed(
			title=f"Leaderboard for {last_saturday()} to {today()}:")

		embed.description = f"{str('🎉')} Congratulations, {', '.join(f'<@{i[1]}>' for i in filter(lambda h: weekly[0][0] == h[0], weekly))}!\n\n" if is_time_for_end_prize else f"{days_remaining} days remaining.\n\n"
		top_user = []
		for i, h in enumerate(weekly):
			prefix = f"{i+1}."
			if is_time_for_end_prize:
				if h[0] == weekly[0][0]:
					prefix = str("🏅")
			embed.description += f"{prefix} <@{int(h[1])}> — {h[0]} hours{str(' ⏲️') if not str(today()) in data[h[1]] else ''}\n"
		await ctx.send(embed=embed)

	@slash.slash(
		name="tex",
		description="Render your message in LaTeX",
		options=[
			manage_commands.create_option(
					name="content",
					description="The LaTeX to be rendered",
					option_type=3,
					required=True,
			)
		],
		guild_ids=[guild_id]
	)
	@bot.command(name="tex", help="render math")
	async def bot_tex(ctx, *, content: str):
		await tex(ctx, content)

	async def tex(ctx, content: str):
		content = content.replace(" ", r"&space;").replace(
			"+", r"&plus;").replace("\n", "")
		math_url = "https://latex.codecogs.com/png.latex?\\bg_white&space;\\LARGE&space;"
		embed = discord.Embed()
		embed.set_image(url=f"{math_url}{content}")
		await ctx.send(embed=embed)

	@slash.slash(
		name="quit",
		description="Close the bot (admin-only)",
		options=[],
		guild_ids=[guild_id]
	)
	@bot.command(name="quit", help="Close the bot (admin-only)")
	async def quit(ctx):
		if ctx.author.id == admin_user_id:
			await ctx.send("Going to sleep now. Goodbye!")
			await bot.logout()
		else:
			await ctx.send("You are not an administrator. Get lost.")

	@slash.slash(
		name="crash",
		description="Exit the bot with exit code 1",
		options=[],
		guild_ids=[guild_id]
	)
	@bot.command(name="crash", help="Exit the bot with exit code 1")
	async def reload(ctx):
		await ctx.send("Crashing…")
		exit(1)

	@slash.slash(
		name="register",
		description="Register a temporary custom command",
		options=[
			manage_commands.create_option(
					name="keyword",
					description="The trigger keyword to use",
					option_type=3,
					required=True
			),
			manage_commands.create_option(
				name="output",
				description="The output to print",
				option_type=3,
				required=True
			),
			manage_commands.create_option(
				name="contains",
				description="Run if this string is detected in any message",
				option_type=3,
				required=False
			),
			manage_commands.create_option(
				name="help",
				description="Help text",
				option_type=3,
				required=False
			)
		],
		guild_ids=[guild_id]
	)
	@bot.command(name="register", help="Register a new temporary custom command")
	async def register(ctx, keyword: str, output: str, contains="", help="A temporary custom command"):
		keyword = keyword.replace(" ", "").replace(command_prefix, "")

		@commands.command(name=keyword, help=help)
		async def c(ctx, *args):
			await ctx.send(output.format(*args))

		try:
			bot.add_command(c)
			slash.add_slash_command(
				c, name=keyword, description=help, options=[], guild_ids=[guild_id])
			command_register.append((c, contains))
			await slash.sync_all_commands()
			await ctx.send(f"Registered new temporary command {keyword}.")
		except commands.CommandRegistrationError:
			await ctx.send(f"The keyword `{keyword}` is already registered as a command and cannot be overwritten.")
		except discord_slash.error.DuplicateCommand:
			await ctx.send(f"The keyword `{keyword}` is already registered as a remote command and cannot be overwritten.")
		except Exception:
			traceback.print_exc()
			await ctx.send(f"Something broke — don't do it again.")
	
	# play, stop
	# later: queue, skip
	async def connect(ctx):
		channel = None
		try:
			channel = ctx.author.voice.channel
		except AttributeError:
			await ctx.send("You are not in a voice channel.")
			return 1
		
		vc = ctx.guild.voice_client
		if vc:
			if vc.channel.id == channel.id:
				vc.stop()
				return vc
			return await vc.move_to(channel)
		else:
			return await channel.connect()
		return
	
	@slash.slash(
		name="play",
		description="Play a Moosic (random if no query)",
		options=[
			manage_commands.create_option(
				name="query",
				description="Title and/or author to search for",
				option_type=3,
				required=False
			),
			manage_commands.create_option(
				name="number",
				description="Song number from search",
				option_type=4,
				required=False
			)
		],
		guild_ids=[guild_id]
	)
	async def play(ctx, query, number:int=1):
		play_random = query == ""
		args = query.lower().split(" ")
		source = None
		name = None
		if not play_random:
			for f in moosics:
				for s in args:
					if not s in moosics[f][1].lower():
						break
				else:
					number -= 1
					if number <= 0:
						source = moosics[f][1]
						name = moosics[f][0].replace(".mp3", "")
						break
			else:
				return await ctx.send(f"`{query}` returned no results.")
		else:
			file = random.choice(list(moosics.keys()))
			name = moosics[file][0].replace(".mp3", "")
			source = moosics[file][1]
		
		vc = await connect(ctx)
		if vc == 1:
			return

		vc = await connect(ctx)
		lyric_client = LyricPlayer(vc, source.replace(".mp3", ".lrc"), bot.get_channel(lyric_channel))
		vc.play(discord.FFmpegPCMAudio(executable="/usr/bin/ffmpeg", source=source))
		loop = asyncio.get_event_loop()
		loop.create_task(lyric_client.start())
		await ctx.send(f"Playing {'random ' if play_random else ''}song: **{name}**.")
		await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.listening, name=name))
		while vc.is_playing(): # not short enough for /play override
			await asyncio.sleep(1)
		lyric_client.stop()
		await vc.disconnect()
		await bot.change_presence(activity=None)
	
	@slash.slash(
		name="search",
		description="Search a Moosic",
		options=[
			manage_commands.create_option(
				name="query",
				description="Title and/or author to search for",
				option_type=3,
				required=True
			),
			manage_commands.create_option(
				name="page",
				description="The page number to skip to",
				option_type=4,
				required=False
			)
		]
	)
	async def search(ctx, query, page:int=1):
		page -= 1
		args = query.lower().split(" ")
		name = []
		for f in moosics:
			for s in args:
				if not s in moosics[f][1].lower():
					break
			else:
				name.append(moosics[f][0].replace(".mp3", ""))
		if len(name) == 0:
			return await ctx.send(f"`{query}` returned no results.")
		embed = discord.Embed(title=f"Moosic containing '{query}'", description="")
		per_page = 10
		offset = page * per_page
		for i, n in enumerate(name):
			if i < offset: continue
			if i > offset + per_page - 1: break
			embed.description += f"{i+1}. {n}\n"
		embed.description += f"\nPage {page+1} of {math.ceil(len(name) / per_page)}"
		await ctx.send(embed=embed)
	
	@slash.slash(
		name="stop",
		description="Stop a Moosic",
		options=[],
		guild_ids=[guild_id]
	)
	@bot.command(name="stop", help="Stop a Moosic")
	async def stop(ctx):
		vc = ctx.guild.voice_client
		if not vc:
			return await ctx.send("No Moosic to stop.")
		await vc.disconnect()
		await bot.change_presence(activity=None)

	@bot.event
	async def on_command(message):
		await message.reply("WARNING: Traditional prefixed commands are deprecated and will be removed in a future update. Please switch to slash commands instead.")

	@bot.event
	async def on_message(message):
		await bot.process_commands(message)
		content = message.content
		if message.author.bot or content.startswith(command_prefix) or content.startswith("</"):
			return
		for c, contains in command_register:
			if contains != "":
				if contains in message.content.lower():
					await c(message.channel, *(content.split()))

		# non-command latex support
		if not "$$" in content:
			return
		content = content.split("$$")

		if len(content) < 3:
			return
		for i, s in enumerate(content):
			if i % 2 == 1:
				await tex(message.channel, s)

	bot.run(client_token)
