import threading
import time
from helpers.constants import *
import sqlite3
import logging
from pubsub import pub

log = logging.getLogger(LOGGER_NAME)

class DBService:

    def __init__(self, configuration, IMEI):
        super().__init__()
        log.debug(f"DBService init ({IMEI})")
        self._running = False
        self.IMEI = IMEI

    def start(self):
        log.info(f"DBService Start ({self.IMEI})")

        try:
            self.DBcon = sqlite3.connect('logs/silenceLOG.db', check_same_thread=False)
            self.cursor = self.DBcon.cursor()
            self.cursor.execute('''CREATE TABLE IF NOT EXISTS "Messaggi" (
                                "id"	INTEGER NOT NULL,
                                "mittente" TEXT NOT NULL,
                                "time"	TEXT NOT NULL,
                                "message"	BLOB,
                                PRIMARY KEY("id" AUTOINCREMENT)
                            );''')

            self.DBcon.commit()
            log.debug(f"Subscribing to {TOPIC_SOCKET} ({self.IMEI})")
            pub.subscribe(self.receive_from_socket, TOPIC_SOCKET)

        except Exception:
            log.exception("Exception in MQTTService start")
            self.DBcon.close()
            return

        self._running = True

        # Start the thread to keep the service running
        thread = threading.Thread(target=self.run)
        thread.daemon = True
        thread.start()

    def stop(self):
        self.DBcon.close()
        self._running = False
        log.info(f"DBService Stop for IMEI: {self.IMEI}")

    def run(self):
        # Loop to keep the service running
        while self._running:
            time.sleep(0.1)

    def receive_from_socket(self, receiver, data):
        log.debug(f"Received message from {receiver}: {data}")
        self._scritturaDB(receiver, data)

    def _scritturaDB(self, mittente, data):
        try:
            self.cursor.execute(""" INSERT INTO "Messaggi" ("time", "mittente", "message") VALUES (datetime('now'), ?, ?)""" , (mittente,data))
            self.DBcon.commit()
        except Exception:
            log.exception("Exception in _scritturaDB")
