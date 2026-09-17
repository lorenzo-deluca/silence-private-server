import logging
import threading
import time
import socket

from pubsub import pub
from _thread import *

from helpers.constants import *
from helpers.messageParser import MessageParser
from helpers.command import Command

from services.CommandService import CommandService
from services.DBService import DBService

log = logging.getLogger(LOGGER_NAME)

class SilenceServerService(threading.Thread):
    def __init__(self, configuration, IMEI):
        super().__init__()

        self.IMEI = IMEI

        log.info(f"Starting SilenceServerService for IMEI: {self.IMEI}")

        self._stop_event = threading.Event()

        self.serverPORT = configuration["serverPORT"]
        self.silenceHOST = configuration["silenceHOST"]
        self.silencePORT = configuration["silencePORT"]
        self.bridgeMode = configuration["bridgeMode"]

        self.keepAliveInterval = configuration["keepAliveInterval"]
        self.connectionCount = 0
        self.connectionLock = threading.Lock()
        self.newConnectionEvent = threading.Event()
        self.ACKresponse = b'\x06'

        self.BMScellVoltage_pooling_interval = configuration["BMScellVoltage_pooling_interval"]

        self.parser = MessageParser()

        if configuration["SaveToDB"]:
            log.info(f"Starting DBService for IMEI: {self.IMEI}")
            self.dbService = DBService(configuration, IMEI)
            self.dbService.start()

        self.commandService = CommandService(configuration, IMEI)
        self.commandService.start()

    def stop(self):
        self._stop_event.set()

        if self.dbService:
            self.dbService.stop()

    def run(self):

        def listenerClient(scooterSocket):
            did_increment_count = False

            try:
                # Once connected verify the login
                if self.bridgeMode:
                    silenceClientSocket = socket.socket()

                messages,crashedFlag = self._telegramReceiver(scooterSocket, 10, 0.2, "A", True)

                if crashedFlag == 1 or len(messages) == 0:
                    raise Exception("crashed socket 1")

                retrievedIMEI = ""
                for message in messages:
                    data = message["data"]
                    protocol = message["protocol"]
                    if protocol == "Astra":
                        decodedData = data.decode()
                        retrievedIMEI = decodedData.split(";")
                        retrievedIMEI = retrievedIMEI[2]
                        log.info ("retrieved IMEI: " + retrievedIMEI)

                # Verify if IMEI is correct
                if retrievedIMEI == self.IMEI:
                    # Signal any existing thread to exit, then take its slot.
                    # Prevents "two valid login" deadlock when an old thread is
                    # blocked on a slow _telegramReceiver and can't observe
                    # connectionCount > 1 in time.
                    with self.connectionLock:
                        if self.connectionCount >= 1:
                            log.info("new login detected, signaling old thread to exit")
                            self.newConnectionEvent.set()
                        self.connectionCount = self.connectionCount + 1
                        did_increment_count = True
                    if self.bridgeMode:
                        log.info("We're in bridge mode, trying to connect to Silence Server")
                        silenceClientSocket.connect((self.silenceHOST, self.silencePORT))  # Trying to connect to Silence Servers.
                        log.info("Connected to Silence Server")

                        for message in messages:
                            data = message["data"]
                            protocol = message["protocol"]
                            silenceClientSocket.send(data)
                        dataFromSilence,crashedFlag = self._telegramReceiver(silenceClientSocket, 10, 0.2, "S")

                        if crashedFlag == 1:
                            raise Exception("crashed socket")

                        elif len(dataFromSilence) > 0:
                            for message in dataFromSilence:
                                data = message["data"]
                                protocol = message["protocol"]
                                scooterSocket.send(data)    # Sending message to scooter
                                log.info("login correct sending ack")
                        else:
                            raise Exception("silence didn't confirm login, closing")

                    else:
                        scooterSocket.send(self.ACKresponse)

                else:           # If IMEI is wrong throw an exception and close everything
                    log.error("no my IMEI")
                    raise Exception("Wrong login")

                # Wait for old socket to close. The old thread has been
                # signaled via newConnectionEvent and should exit promptly.
                # Generous timeout (30s) covers slow socket closes; if it still
                # doesn't drop, force the count down to 1 ourselves rather than
                # crash-looping (which is what caused the historical deadlock).
                wait_counter = 0
                while self.connectionCount > 1:
                    wait_counter += 1
                    if wait_counter > 300:
                        log.warning("old thread did not exit in 30s, forcing connectionCount reset")
                        with self.connectionLock:
                            self.connectionCount = 1
                        break
                    time.sleep(0.1)
                # We are now the active thread; clear the event for future logins
                self.newConnectionEvent.clear()

                last_keep_alive_sent = time.time()
                last_BMS_pooling_time = 0
                while 1:    # Communication loop with scooter
                    # See in scooter has data to send
                    messages,crashedFlag = self._telegramReceiver(scooterSocket, 0.2, 0.2, "A", True)
                    if crashedFlag == 1:
                        log.error("crash socket on communication from scooter")
                        raise Exception("crash socket on communication from scooter")
                    elif len(messages) > 0:
                        for message in messages:
                            data = message["data"]
                            protocol = message["protocol"]
                            log.info("received status from scooter")
                            if message["protocol"] == "Z":  #only parse protocol Z messages
                                self.parser.parse_message_from_scooter_protocol_Z(data)
                            elif message["protocol"] == "Astra":
                                self.parser.parse_message_from_scooter_protocol_astra(data)
                            if self.bridgeMode and len(data) > 1:   #hide response ack to silence
                                silenceClientSocket.send(data)
                                log.info("sent to silence")
                            elif len(data) > 1:     #also don't send an ACK in response to an ACK
                                scooterSocket.send(self.ACKresponse)
                                log.info("sent ack to scooter")
                    #else:
                    #    log.info("No data from Scooter")

                    # Checking if we have any command to send to the scooter
                    while(self.commandService.commands_to_execute()):
                        command = self.commandService.get_next_command()
                        log.info(f"command to execute: {command.Code}")
                        try:
                            log.info(f"Sending command to scooter: {command.get_command()}")
                            scooterSocket.send(command.get_command())
                            log.info(f"sent command to scooter")
                            self.commandService.command_executed(command, "Ok".encode())
                        except Exception as e:
                            log.error(f"Command {command.Code} execution failed: {e}")
                            self.commandService.command_failed(command)
                            continue

                    if self.BMScellVoltage_pooling_interval > 0 and time.time() - last_BMS_pooling_time > self.BMScellVoltage_pooling_interval and not self.parser.scooter_off:
                        scooterSocket.send("$RCAN,185".encode())
                        scooterSocket.send("$RCAN,186".encode())
                        scooterSocket.send("$RCAN,187".encode())
                        scooterSocket.send("$RCAN,188".encode())
                        # Extended CAN: ECU mode/switches, range, BMS flags
                        # Small delay between sends to avoid overwhelming the Astra module
                        time.sleep(0.05)
                        scooterSocket.send("$RCAN,182".encode())
                        time.sleep(0.05)
                        scooterSocket.send("$RCAN,280".encode())
                        time.sleep(0.05)
                        scooterSocket.send("$RCAN,300".encode())
                        # Decoded CAN sensors: BMS current, temperatures, Votol RPM/power/voltage
                        time.sleep(0.05)
                        scooterSocket.send("$RCAN,181".encode())
                        time.sleep(0.05)
                        scooterSocket.send("$RCAN,189".encode())
                        time.sleep(0.05)
                        scooterSocket.send("$RCAN,371".encode())
                        time.sleep(0.05)
                        scooterSocket.send("$RCAN,381".encode())
                        time.sleep(0.05)
                        scooterSocket.send("$RCAN,391".encode())
                        last_BMS_pooling_time = time.time()

                    if self.keepAliveInterval > 0 and time.time() - last_keep_alive_sent > self.keepAliveInterval:
                        scooterSocket.send(self.ACKresponse)
                        log.info("Keep-Alive: Sent ACK to scooter")
                        last_keep_alive_sent = time.time()

                    if self.bridgeMode:
                        # Checking if Silence has to send anything to the scooter
                        dataFromSilence,crashedFlag = self._telegramReceiver(silenceClientSocket, 0.2, 0.2, "S")
                        if crashedFlag == 1:
                            raise Exception("crashed socket")

                        elif len(dataFromSilence) > 0:
                            for message in dataFromSilence:
                                data = message["data"]
                                protocol = message["protocol"]
                                if data.decode()[:5] == "$RCAN": # Don't send Silence the magic tricks we do on CAN
                                    continue
                                scooterSocket.send(data)
                                log.info(f"sent to scooter the data from silence {data}")

                        #else:
                        #    log.info("no data from silence")

                    if self.newConnectionEvent.is_set():     # A new login arrived; yield our slot.
                        raise Exception("new connection detected, closing the old one")

            except Exception:
                log.exception ("Exception on listener")

                try:
                    scooterSocket.close()
                except:
                    log.error("socket scooter already close")

                try:
                    silenceClientSocket.close()
                except:
                    log.error("socket silence server already close")

                if did_increment_count:
                    with self.connectionLock:
                        self.connectionCount = max(0, self.connectionCount - 1)
                log.info("closing thread, connected clients: "+str(self.connectionCount))

        #------------------------------------- LISTENING TO INCOMING CONNECTIONS ---------------------------------------
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)   #configure socket
        s.bind(("", self.serverPORT))    # Restrict connections to our host only
        s.settimeout(60)

        log.info(f"socket created on port {self.serverPORT}")

        while (1):  # loop to recreate listening socket on disconnect

            try:    # attempting to connect
                s.listen()
                log.info("waiting for clients")
                while (1):
                    try:
                        conn, addr = s.accept()
                        log.info("client "+str(addr)+" connected")
                        start_new_thread(listenerClient, (conn, ))
                        log.info("client connnected: "+str(self.connectionCount))
                        time.sleep(0.01)
                    except:
                        time.sleep(0.01)
                        continue

            except Exception:
                log.exception ("connection Exception on listener")
                time.sleep(0.1)
                s.close()

    def _telegramReceiver(self,scooterSocket,primoTimeout,secondoTimeout,receiverName,firstFrame = False):

        def _messageReceived(messages,data,protocol):
            messages.append({"protocol" : protocol , "data" : data})
            log.info(f"receive data {data}")
            pub.sendMessage(TOPIC_SOCKET, receiver = receiverName, data=data)


        scooterSocket.settimeout(primoTimeout)
        messages = []

        while 1:
            try:
                data = bytearray()
                oldDataLen = len(data)
                protocol = ""
                byte = scooterSocket.recv(1)
                if not byte:    # recv() returned b'': peer closed the connection (EOF)
                    log.error(f"socket closed by peer (EOF) on receiver {receiverName}")
                    return messages,1
                data += byte

                if len(data) > 0 and int(data[-1]) == 36: #message starting with $
                    protocol = "Astra"
                elif int(data[-1]) == 90:   #message starting with Z
                    protocol = "Z"
                elif int(data[-1]) == 6:    #ACK message
                    _messageReceived(messages,data,"ACK")
                    continue
                scooterSocket.settimeout(secondoTimeout)
                while 1:
                    byte = scooterSocket.recv(1)
                    if not byte:    # recv() returned b'': peer closed mid-frame (EOF)
                        log.error(f"socket closed by peer (EOF) mid-frame on receiver {receiverName}")
                        return messages,1
                    data += byte
                    if protocol == "Astra" and int(data[-1]) == 10 and int(data[-2]) == 13: #check for end of message started with $
                        _messageReceived(messages,data,protocol)
                        break
                    elif protocol == "Z" and len(data) >= 3 and int.from_bytes(data[1:3], byteorder='big') == len(data):    #check for end of message started with Z
                        _messageReceived(messages,data,protocol)
                        break

                    oldDataLen = len(data)

            except socket.timeout:
                if len(data) > 0:
                    if protocol == "Astra" and len(data) >= 2 and int(data[-1]) == 10 and int(data[-2]) == 13:
                        # Trame Astra complète arrivée pile au timeout
                        _messageReceived(messages,data,protocol)
                    elif receiverName != "A":
                        # Côté Silence officiel (bridge) : comportement historique
                        _messageReceived(messages,data,protocol)
                    else:
                        # Trame incomplète (coupure GSM / bus CAN en vrac) ou
                        # octets hors protocole (scanners Internet sur le port
                        # exposé) : on ne transmet RIEN au parseur ni à la base
                        # — les trames tronquées produisaient des valeurs
                        # aberrantes (odo à 980M via split() désaligné) et le
                        # junk des scanners polluait le cache et la DB.
                        log.warning(f"dropping invalid/incomplete telegram ({len(data)} bytes, protocol='{protocol or 'none'}') on receiver {receiverName}")
                        if not messages:
                            # Round sans AUCUN message valide : connexion
                            # étrangère (scanner) -> on la ferme immédiatement.
                            return messages,1
                return messages,0

            except OSError as e:    # ConnectionResetError, broken pipe, closed socket, ...
                log.error(f"socket error on receiver {receiverName}: {e}")
                return messages,1

            except Exception:
                log.exception("_telegramReceiver exception")
                return messages,1   # If there was no timeout exception, the socket is blocked.
