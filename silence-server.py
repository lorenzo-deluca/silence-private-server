import json
import time
import platform
from concurrent.futures import ThreadPoolExecutor

from helpers.constants import *
from helpers.logger import setup_logger
from services.MQTTService import MQTTService
from services.SilenceServerService import SilenceServerService

def start_services_for_imei(configuration, imei, index, logger):
    """Start Silence Server and MQTT Service for a given IMEI number."""
    logger.info(f"Logger configured for IMEI: {imei}")

    # Verwende BasePort und erzeuge eindeutige Ports für jede IMEI
    port = configuration["TCPServer"]["BasePort"] + index
    logger.info(f"Using port {port} for IMEI: {imei}")

    # Start Silence Server mit dem individuellen Port
    server_config = configuration["TCPServer"].copy()
    server_config["serverPORT"] = port  # Fügt serverPORT hinzu

    server = SilenceServerService(server_config, imei)
    server.start()

    # Start MQTT Service if enabled
    mqttService = None
    if configuration["MQTT"]["Enabled"]:
        logger.info(f"Starting MQTT for IMEI: {imei}")
        mqttService = MQTTService(configuration["MQTT"], imei)
        mqttService.start()

    return server, mqttService

def main():
    print("Silence-Server Main")

    with open("configuration.json") as configurationFile:
        configuration = json.load(configurationFile)
        print(f"Configuration loaded: {configuration}")

    # Creating and configuring logger
    logger = setup_logger(configuration["Logging"])
    logger.debug("Logger configured")

    # Starting services for each IMEI in separate threads
    imei_list = configuration["IMEI_List"]  # List of IMEI from the configuration file
    logger.info(f"Starting services for IMEI list: {imei_list}")

    servers_mqtt_services = []
    with ThreadPoolExecutor(max_workers=len(imei_list)) as executor:
        futures = []
        for index, imei in enumerate(imei_list):
            future = executor.submit(start_services_for_imei, configuration, imei, index, logger)
            futures.append(future)

        for future in futures:
            servers_mqtt_services.append(future.result())

    logger.info("All Silence Servers and MQTT Services started...")

    try:
        while True:
            time.sleep(0.1)
    except KeyboardInterrupt:
        print("KeyboardInterrupt in main, terminating...")

        # Stopping all Server- und MQTT Services
        for server, mqttService in servers_mqtt_services:
            if server:
                server.stop()
            if mqttService:
                mqttService.stop()

        exit(0)

if __name__ == "__main__":
    print(f"Python version: {platform.python_version()}")
    print(f"Silence-Server version: {VERSION}")

    try:
        print("Silence-Server - Main Start")
        main()
        print("Silence-Server - Main End")
    except Exception as ex:
        print("Silence-Server - Exception in main, restart...")
        print(ex)
