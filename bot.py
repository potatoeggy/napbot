#!/usr/bin/python

import os
import sys
import json
import discord
from discord.ext import commands, tasks
import requests
import time
import datetime

VERBOSE = True
# TODO: add ping reminder at noon for those who have not submitted to submit

def debug(string, urgent=False):
	if urgent:
		print(string)
	elif VERBOSE:
		print("DEBUG:", string)

def show_help(): # TODO: consider adding
	debug("Exiting for showing help", urgent=True)
	exit()

def today():
	return datetime.date.today()

def last_saturday():
	return today() - datetime.timedelta((today().weekday()+1) % 7 + 1)

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

def push_data(discord_id: int, hours: int, data, data_file): # TODO: add previous day support
	discord_id = str(discord_id)
	data[discord_id][str(today())] = hours
	with open(data_file, "w") as file:
		file.write(json.dumps(data, indent=4))

def register_commands(client, client_id, client_token, guild_id, everywhere=False):
	url = f"https://discord.com/api/v8/applications/{client_id}/"
	if not everywhere:
		url += f"guilds/{guild_id}/"
	url += "commands"

	json = {
		"name": "islept",
		"description": "Record the number of hours you slept last night",
		"options": [
			{
				"name": "hours",
				"description": "The number of hours",
				"type": 1,
				"required": True,

			}
		]
	}

	headers = {
		"Authorization": "Bot " + client_token
	}

	r = requests.post(url, headers=headers, json=json)
	debug("Registered required commands.")

def init():
	# TODO: import from env vars
	# load conf file
	debug("Reading configuration...", urgent=True)
	config_file = os.path.join(sys.path[0], "config.json")
	try:
		config_file = sys.argv[sys.argv.index("--config") + 1]
	except ValueError:
		pass # user did not specify custom conf location
	except IndexError:
		show_help()
	
	jsondict = {}
	try:
		with open(config_file, "r") as file:
			data = file.read()
		jsondict = json.loads(data)
	except EnvironmentError:
		debug("Could not find config file.") # config file is probably not found and so will use fallback

	def check_config(key, fallback=None):
		result = jsondict[key] if key in jsondict else fallback
		args_key = "--" + key.replace("_", "-")
		try:
			if type(fallback) is not bool:
				result = type(fallback)(sys.argv[sys.argv.index(args_key) + 1])
			elif args_key in sys.argv:
				result = True
		except ValueError:
			pass # user did not specify conf in command line
		except IndexError:
			show_help()
		return result
	
	# mandatory fields
	discord_id = check_config("discord_id")
	discord_token = check_config("discord_token")
	discord_guild = check_config("discord_guild")
	admin_user_id = check_config("admin_user_id")
	VERBOSE = check_config("verbose", False)

	# load saved data
	debug("Reading data...")
	data_file = check_config("data_file", os.path.join(sys.path[0], "data.json"))
	data = {}
	try:
		with open(data_file, "r") as file:
			data = file.read()
		data = json.loads(data)
	except EnvironmentError:
		debug("Could not find data file. Exiting...", urgent=True) # data file is probably not found and so will crash
		exit()
	
	return discord_guild, discord_token, data, data_file, admin_user_id

if __name__ == "__main__":
	guild_id, client_token, data, data_file, admin_user_id = init()
	bot = commands.Bot(command_prefix="!")

	@bot.event
	async def on_ready():
		guild = discord.utils.get(bot.guilds, id=guild_id)
		debug(f"{bot.user} connected to Discord to {guild} (id: {guild_id}).", urgent=True)

	@bot.command(name="islept", help="Records the number of hours you slept last night.")
	async def save_hours(ctx, hours_slept: int, user_override=None):
		sender = int(user_override) if user_override != None else ctx.message.author.id
		if user_override != None and ctx.message.author.id != admin_user_id:
			await ctx.send(f"ERROR: {ctx.message.author} does not have override permissions.")
			return
		if not 0 <= hours_slept <= 11:
			await ctx.send(f"ERROR: {hours_slept} hours is not in the range of 0 to 11 hours.")
			return
		
		push_data(sender, hours_slept, data, data_file)
		await ctx.message.add_reaction("✅")

	@bot.command(name="slept", help="Alias for islept")
	async def save_hours2(ctx, hours_slept: int, user_override=None):
		await save_hours(ctx, hours_slept, user_override)
	
	@bot.command(name="s", help="Alias for islept")
	async def save_hours3(ctx, hours_slept: int, user_override=None):
		await save_hours(ctx, hours_slept, user_override)

	@bot.command(name="stats", help="Shows sleep statistics for a user")
	async def stats(ctx, target_id: int =None): #TODO: implement
		sender = ctx.message.author if target_id == None else await bot.fetch_user(target_id)
		embed = discord.Embed(title=f"Sleep statistics for {sender.name}:")
		embed.add_field(name="Cumulative hours slept:", value=cumulative(sender.id, data), inline=False)
		embed.add_field(name="Average of cumulative hours slept:", value=cumulative_average(sender.id, data), inline=False)
		embed.add_field(name="Hours slept this month:", value=cumulative_month(sender.id, data), inline=False)
		embed.add_field(name="Hours slept this week:", value=cumulative_week(sender.id, data), inline=False)
		embed.add_field(name="Hours slept last night:", value=hours_today(sender.id, data), inline=False)
		await ctx.send(embed=embed)

	@bot.command(name="me", help="Alias for stats <your_id>")
	async def stats2(ctx):
		await stats(ctx)

	@bot.command(name="leaderboard", help="Show everyone's weekly sleep stats")
	async def leaderboard(ctx, board="weekly"): # TODO: implement
		# TODO: implement non-weekly leaderboards
		weekly = []
		days_remaining = 7-(today()-last_saturday()).days
		for i, d in data.items():
			weekly.append((cumulative_week(str(i), data), i))
		weekly.sort(reverse=True)
		embed = discord.Embed(title=f"Leaderboard for {last_saturday()} to {today()}:")
		embed.description = f"{days_remaining} days remaining.\n\n"
		for i, h in enumerate(weekly):
			embed.description += f"{i+1}. <@{int(h[1])}> — {h[0]} hours{str(' ⏲️') if not str(today()) in data[h[1]] else ''}\n"
		await ctx.send(embed=embed)
	
	@bot.command(name="tex", help="render math")
	async def bot_tex(ctx, *, content: str):
		await tex(ctx, content)

	async def tex(ctx, content: str):
		content = content.replace(" ", r"&space;").replace("+", r"&plus;").replace("\n", "")
		math_url = "https://latex.codecogs.com/png.latex?\\bg_white&space;\\LARGE&space;"
		embed = discord.Embed()
		embed.set_image(url=f"{math_url}{content}")
		await ctx.send(embed=embed)
	
	@bot.command(name="quit", help="Exit")
	async def quit(ctx):
		await ctx.send("Going to sleep now. Goodbye!")
		await bot.logout()

	@bot.event
	async def on_message(message):
		await bot.process_commands(message)
		content = message.content
		if not "$$" in content:
			return
		content = content.split("$$")

		if len(content) < 3:
			return
		for i, s in enumerate(content):
			if i % 2 == 1:
				await tex(message.channel, s)

	bot.run(client_token)
