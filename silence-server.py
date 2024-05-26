import json
import time
import platform

from helpers.constants import *
from helpers.logger import setup_logger

from services.MQTTService import MQTTService
from services.SilenceServerService import SilenceServerService

def main():

    print ("Silence-Server Main")

    with open("configuration.json") as configurationFile:
        configuration = json.load(configurationFile)
        print(f"Configuration loaded: {configuration}")

    logger = setup_logger(configuration["Logging"])
    logger.debug("Logged configured")

    logger.info("Starting Silence Server")
    server = SilenceServerService(configuration["TCPServer"], configuration["IMEI"])
    server.start()

    if configuration["MQTT"]["Enabled"]:
        logger.info("Starting MQTT")
        mqttService = MQTTService(configuration["MQTT"], configuration["IMEI"])
        mqttService.start()

    logger.info("Silence Server Started...!")

    try:
        while True:
            time.sleep(0.1)
            pass
    except KeyboardInterrupt:
        print("KeyboardInterrupt in main, terminate...")
        
        if server:
            server.stop()

        if mqttService:
            mqttService.stop()
        
        exit(0)

if __name__ == "__main__":

    print (f"Python version: {platform.python_version()}")
    print (f"Silence-Server version: {VERSION}")

    try:
        print("Silence-Server - Main Start")
        main()
        print("Silence-Server - Main End")
    except Exception as ex:
        print("Silence-Server - Exception in main, restart...")
        print(ex)