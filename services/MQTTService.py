from helpers.constants import *
import paho.mqtt.client as mqtt
import logging
import time
from pubsub import pub
from helpers.command import Command

log = logging.getLogger(LOGGER_NAME)

class MQTTService:

    def __init__(self, configuration, IMEI):
        self.broker = configuration["MQTTbroker"]
        self.port = configuration["MQTTport"]
        self.username = configuration["MQTTuser"]
        self.password = configuration["MQTTpass"]
        self.password = configuration["MQTTpass"]
        self.prefix = configuration["TopicPrefix"] + "/" + IMEI

        self.client = mqtt.Client()

        self.last_status = {}
        log.debug("MQTT Service initialized")

    def build_topic(self, topic):
        return f"{self.prefix}/{topic}"

    def disconnect(self):
        self.client.disconnect()

    def publish(self, topic, value):
        log.debug(f"Publishing on topic [{topic}] value: {value}")
        self.client.publish(topic, value)

    def on_connect(self, client, userdata, flags, rc):
        log.info(f"Connected to broker with result code {rc}")

        log.debug(f"MQTT Sub to {self.build_topic(MQTT_COMMAND)}/+")
        client.subscribe(f"{self.build_topic(MQTT_COMMAND)}/+")

    def on_message(self, client, userdata, message):
        log.debug(f"Received message '{message.payload.decode()}' on topic '{message.topic}'")

        cmd = message.topic.replace(self.build_topic(MQTT_COMMAND)+"/", "")
        if len(cmd) > 0:
            try:
                pub.sendMessage(TOPIC_COMMAND_RECEIVED, command=cmd, payload=message.payload.decode())
            except Exception as ex :
                log.error(f"Invalid command received: {cmd}")
                self.publish(f"{self.build_topic(MQTT_COMMAND)}/{cmd}/{MQTT_RESULT}", f"Invalid command {cmd}")
                return

    def start(self):

        log.info(f"Start MQTT Service")
        try:
            self.client.username_pw_set(self.username, self.password)
            self.client.connect(self.broker, self.port, 60)
            self.client.on_connect = self.on_connect
            self.client.on_message = self.on_message

            log.debug(f"Pub/Sub on {TOPIC_SCOOTER_STATUS}")
            pub.subscribe(self.publish_scooter_status, TOPIC_SCOOTER_STATUS)

            log.debug(f"Pub/Sub on {TOPIC_COMMAND_RESULT}")
            pub.subscribe(self.command_result, TOPIC_COMMAND_RESULT)

            self.client.loop_start()
            
        except Exception:
            log.exception("Exception in MQTTService start")
            raise Exception("Exception in MQTTService start")

    def stop(self):
        log.info("Stopping MQTT Service")
        self.client.loop_stop()
    
    def command_result(self, command, result):
        log.debug(f"Command {command} result {result}")
        self.publish(f"{self.build_topic(MQTT_COMMAND)}/{command}/{MQTT_RESULT}", result)
        
    def publish_scooter_status(self, scooter_status):
        log.debug(f"Publishing scooter status: {scooter_status}")
        
        for parameter in scooter_status:
            try:
                if scooter_status[parameter]["value"] != None:
                    MQTT_topic = scooter_status[parameter]["MQTT_topic"]
                    value = scooter_status[parameter]["value"]
                    self.publish(f"{self.build_topic(MQTT_STATUS)}/{MQTT_topic}",value)
            except Exception:
                log.exception(f"Exception in publishing parameter {parameter}")

        self.last_status = scooter_status
        self.lastMessageTime = time.time()
        self.publish(f"{self.build_topic(MQTT_STATUS)}/last-update", self.lastMessageTime)
