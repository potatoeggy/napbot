import os
import sys
import json
import discord
from discord.ext import commands
import requests
import time

VERBOSE = True

def debug(string, urgent=False):
	if urgent:
		print(string)
	elif VERBOSE:
		print("DEBUG:", string)

def show_help(): # TODO: consider adding
	debug("Exiting for showing help", urgent=True)
	exit()


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

	bot = commands.Bot(command_prefix="!")

	@bot.event
	async def on_ready():
		guild = discord.utils.get(bot.guilds, id=discord_guild)
		debug(f"{bot.user.name} connected to Discord to {guild}.", urgent=True)

	@bot.command(name="islept", help="Records the number of hours you slept last night.")
	async def save_hours(ctx, hours_slept: int):
		response = f"Recorded {hours_slept} hours."
		await ctx.send(response)

	@bot.command(name="slept", help="Alias for islept")
	async def save_hours2(ctx, hours_slept: int):
		await save_hours(ctx, hours_slept)
	
	@bot.command(name="s", help="Alias for islept")
	async def save_hours3(ctx, hours_slept: int):
		await save_hours(ctx, hours_slept)

	@bot.command(name="me", help="Show your sleep statistics")
	async def stats(ctx): #TODO: implement
		pass

	@bot.command(name="leaderboard", help="Show everyone's sleep statistics")
	async def leaderboard(ctx): # TODO: implement
		pass

	bot.run(discord_token)
	return bot, data

if __name__ == "__main__":
	bot, data = init()