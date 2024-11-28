import json
import threading
import time
import logging
import os
from pubsub import pub
from queue import Queue

from helpers.constants import *
from helpers.command import Command

log = logging.getLogger(LOGGER_NAME)

class CommandService:

    def __init__(self, configuration, IMEI):
        super().__init__()
        log.debug(f"CommandService init ({IMEI})")
        self.command_queue = Queue()
        self._running = False
        self.IMEI = IMEI

    def start(self):

        try:
            # load message parsing configuration
            with open(os.path.join("helpers", "commands_definition.json")) as commands_definition:
                self.commands_definition = json.load(commands_definition)

            # add default "RAW_COMMAND" command
            self.commands_definition[RAW_COMMAND_CODE] = {'timeout': 10, 'retry' : 1, 'command': ''}

            log.debug(f"Subscribing to {TOPIC_COMMAND_RECEIVED} ({self.IMEI})")
            pub.subscribe(self.command_received, TOPIC_COMMAND_RECEIVED)

            self._running = True

            thread = threading.Thread(target=self.run)
            thread.daemon = True
            thread.start()

            log.info(f"CommandService Started ({self.IMEI})")

        except Exception:
            log.exception("Exception in CommandService start")
            return

    def stop(self):
        self._running = False
        log.info(f"CommandService Stopped ({self.IMEI})")

    def run(self):
        while self._running:
            self.cleanup_queue()
            time.sleep(30)

    def command_received(self, command, payload):
        log.info(f"Command {command} received: {payload}")

        # verify command in configuration
        if command not in self.commands_definition:
            log.error(f"Invalid command received: {command}")
            pub.sendMessage(TOPIC_COMMAND_RESULT, command=command, result="Invalid command")
            return

        # verify command already in queue
        if any(item.Code == command for item in list(self.command_queue.queue)):
            log.error(f"Command already in queue: {command}")
            pub.sendMessage(TOPIC_COMMAND_RESULT, command=command, result="Command already in queue")
            return

        try:
            if command == RAW_COMMAND_CODE:
                log.info(f"Raw command received: {payload}")
                command_config = json.loads(payload)
            else:
                command_config = self.commands_definition[command]

            command_to_insert = Command(command, command_config)
            self.command_queue.put(command_to_insert)

            log.info(f"Command inserted in queue: {command_to_insert.to_json()} ({imei})")

        except Exception as ex:
            log.exception("Exception in command_received")
            raise ValueError(f"Command not configurated: {command}, error {ex}")

    def cleanup_queue(self):
        for item in list(self.command_queue.queue):
            if (item.checkTimeout()):
                log.error(f"Command timeout reached: {item.to_json()} ({imei})")
                #self.command_queue.task_done()
                self.command_queue.queue.remove(item)
                pub.sendMessage(TOPIC_COMMAND_RESULT, command=item.Code, result=f"Command timeout reached, {item.to_json()} ({imei})")

    def get_next_command(self):
        while not self.command_queue.empty():
            next_command = self.command_queue.get()
            if (next_command.checkTimeout()):
                log.error(f"Command timeout reached: {next_command.to_json()} ({imei})")
                self.command_queue.task_done()
                pub.sendMessage(TOPIC_COMMAND_RESULT, command=next_command.Code, result=f"Command timeout reached, {next_command.to_json()} ({imei})")

            return next_command

        return None

    def commands_to_execute(self):
        return not self.command_queue.empty()

    def command_executed(self, command, response):
        log.info(f"Command executed: {command.to_json()} ({imei})")
        self.command_queue.task_done()
        pub.sendMessage(TOPIC_COMMAND_RESULT, command=command.Code, result=f"Response <{response.decode()}> from IMEI {imei}")

    def command_failed(self, command):
        log.error(f"Command failed: {command.to_json()} ({imei})")
        self.command_queue.task_done()

        if (not command.retry()):
            log.error(f"Command retry limit reached: {command.to_json()} ({imei})")
            pub.sendMessage(TOPIC_COMMAND_RESULT, command=command.Code, result=f"Command retry limit reached: {command.to_json()} ({imei})")
        else:
            log.info(f"Command retry: {command.to_json()} ({imei})")
            self.command_queue.put(command)
