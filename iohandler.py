import configparser
import os


class Logger:
    LOG_STRINGS = ["DEBUG", " INFO", " WARN", "ERROR"]
    DEBUG_LEVEL = 0
    INFO_LEVEL = 1
    WARN_LEVEL = 2
    ERROR_LEVEL = 3

    def __init__(self, log_level: int = 0):
        self.log_level = log_level

    def _log(self, msg, log_level: int):
        if log_level >= self.log_level:
            print(f"{Logger.LOG_STRINGS[log_level]}: {msg}")

    def debug(self, msg):
        self._log(msg, Logger.DEBUG_LEVEL)

    def info(self, msg):
        self._log(msg, Logger.INFO_LEVEL)

    def warn(self, msg):
        self._log(msg, Logger.WARN_LEVEL)

    def error(self, msg, abort: bool = False):
        self._log(msg, Logger.ERROR_LEVEL)
        if abort:
            exit(1)

    def set_log_level(self, log_level: int):
        self.log_level = log_level


class Config:
    def __init__(self, log: Logger):
        self.config = configparser.ConfigParser()
        self.read()

    def read(self):
        self.config.read(
            ["config.ini", os.path.abspath(os.path.dirname(__file__)) + "config.ini"]
        )
        general = self.config["napbot"]
        self.log_level = general.getint("LogLevel", fallback=1)
        self.admin_ids = list(map(int, general.get("AdminIds", fallback="").split(",")))
        self.debug_guilds = list(
            map(int, general.get("DebugGuilds", fallback="").split(","))
        )
        self.bot_token = general.get("BotToken")
        self.modules = general.get("Modules", fallback="").split(",")
