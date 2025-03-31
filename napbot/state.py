from . import iohandler

log = iohandler.Logger()
config = iohandler.Config(log)

log.set_log_level(config.log_level)
