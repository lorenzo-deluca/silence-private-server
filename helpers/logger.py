import os
import pytz
import logging
import logging.handlers
from helpers.constants import LOGGER_NAME
from datetime import datetime

def setup_logger(configuration):

    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.DEBUG)

   # Define a custom time formatter that considers the given timezone
    class TimezoneFormatter(logging.Formatter):
        def __init__(self, fmt=None, datefmt=None, tz=None):
            super().__init__(fmt, datefmt)
            self.tz = tz

        def formatTime(self, record, datefmt=None):
            dt = datetime.fromtimestamp(record.created, self.tz)
            formatted_time = dt.strftime('%Y-%m-%d %H:%M:%S.%f')
            formatted_time = formatted_time[:-3]
            timezone_abbr = dt.tzname()
            return f"{formatted_time} {timezone_abbr}"

    # Get the timezone object
    timezone_obj = pytz.timezone(configuration["Timezone"])
    formatter = TimezoneFormatter('%(asctime)s - [%(module)s] - %(levelname)s - %(message)s', tz=timezone_obj)

    # logging to file
    if (configuration["LogToFile"]):
        log_path = 'logs'
        os.makedirs(log_path, exist_ok=True)
        log_file_path = os.path.join(log_path, 'silence-server.log')
        file_handler = logging.handlers.TimedRotatingFileHandler(log_file_path, when='midnight', interval=1, backupCount=10)
        file_handler.setLevel(configuration["LogLevel"])
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    # logging to console
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.DEBUG)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    return logger
