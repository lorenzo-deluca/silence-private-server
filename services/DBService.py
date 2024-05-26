import threading
import time
from helpers.constants import *
import sqlite3
import logging
from pubsub import pub

log = logging.getLogger(LOGGER_NAME)

class DBService:

    def __init__(self):
        super().__init__()
        log.debug("DBService init")
        self._running = False

    def start(self):
        log.info(f"DBService Start")
        
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
            log.debug(f"Subscribing to {TOPIC_SOCKET}")
            pub.subscribe(self.receive_from_socket, TOPIC_SOCKET)

        except Exception:
            log.exception("Exception in MQTTService start")
            self.DBcon.close()
            return

        self._running = True

        # Avvia il thread per mantenere il servizio attivo
        thread = threading.Thread(target=self.run)
        thread.daemon = True
        thread.start()
    
    def stop(self):
        self.DBcon.close()
        self._running = False
        log.info(f"DBService Stop")

    def run(self):
        # Loop per mantenere il servizio attivo
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
