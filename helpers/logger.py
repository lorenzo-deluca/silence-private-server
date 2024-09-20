import os
import logging
import logging.handlers
from helpers.constants import LOGGER_NAME

def setup_logger(configuration):

    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.DEBUG)
    formatter = logging.Formatter('%(asctime)s - [%(module)s] - %(levelname)s - %(message)s')

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
