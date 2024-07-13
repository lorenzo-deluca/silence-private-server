from helpers.constants import *
import json
import logging
import os

from pubsub import pub

log = logging.getLogger(LOGGER_NAME)

class MessageParser:

    def __init__(self):

        self.scooter_off = True
        self.off_statuses = [0,1,5]

        # load message parsing configuration
        with open(os.path.join(os.path.dirname(__file__), "Z_protocol_message_decode.json")) as message_configuration:
            self.message_decode = json.load(message_configuration)

        with open("scooter_status_definition.json") as status_definition:
            self.parameters = json.load(status_definition)

        with open(os.path.join(os.path.dirname(__file__), "RCAN_definition.json")) as RCAN_message_configuration:
            self.RCAN_message_configuration = json.load(RCAN_message_configuration)
    
    def parse_message_from_scooter_protocol_Z(self, data):
        
        if len(data) > 0:
            log.debug(f"Parse received message protocol Z from scooter: {data}")
            try:
                for parameter in self.message_decode:
                    if self.message_decode[parameter]["disable_when_off"] and self.scooter_off:
                        self.parameters[parameter]["value"] = "None"
                    else:
                        for message_type in self.message_decode[parameter]["message_type"]:
                            try:
                                if message_type["message_first_char"] == int(data[0]) and len(data) in message_type["message_lenght"]:
                                    byte_start = message_type["message_byte_pos"][0]
                                    byte_end = message_type["message_byte_pos"][1]+1
                                    value = data[byte_start:byte_end]
                                    if self.message_decode[parameter]["data_type"] == "boolean":
                                        self.parameters[parameter]["value"] = int(value[0] & (1 << self.message_decode[parameter]["bit_pos"]) != 0)
                                    elif self.message_decode[parameter]["data_type"] == "numeric":
                                        self.parameters[parameter]["value"] = int.from_bytes(value, byteorder='big',signed=True) / self.message_decode[parameter]["divider"]
                                        if parameter == "status" and self.parameters[parameter]["value"] in self.off_statuses:
                                            self.scooter_off = True
                                        elif parameter == "status" and self.parameters[parameter]["value"] not in self.off_statuses:
                                            self.scooter_off = False
                                    elif self.message_decode[parameter]["data_type"] == "text":
                                        self.parameters[parameter]["value"] = str(value.decode())
                            except Exception:
                                log.exception(f"Exception in parsing parameter {parameter}")


                log.debug(f"Message protocol Z parsed: {self.parameters}")            
                pub.sendMessage(TOPIC_SCOOTER_STATUS, scooter_status = self.parameters)

            except Exception:
                log.exception(f"Exception in handling message protocol Z {data}")

    def parse_message_from_scooter_protocol_astra(self, data):
        
        if len(data) > 0:
            log.debug(f"Parse received message protocol astra from scooter: {data}")
            try:
                data = data.decode()
                for parameter in self.RCAN_message_configuration:
                    if data[:len(self.RCAN_message_configuration[parameter]["header"])] == self.RCAN_message_configuration[parameter]["header"]:
                        byte_pos = self.RCAN_message_configuration[parameter]["message_byte_pos"]
                        positions = data.split(",")
                        combined_HEX = positions[byte_pos[1]] + positions[byte_pos[0]]
                        self.parameters[parameter]["value"] = int(combined_HEX, 16)/1000

                log.debug(f"Message protocol astra parsed: {self.parameters}")            
                pub.sendMessage(TOPIC_SCOOTER_STATUS, scooter_status = self.parameters)

            except Exception:
                log.exception(f"Exception in handling message protocol astra {data}")

    def get_scooter_off_status(self):
        return self.scooter_off