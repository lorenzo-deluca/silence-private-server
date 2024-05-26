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

        log.info("Starting SilenceServerService")

        self._stop_event = threading.Event()

        self.IMEI = IMEI
        self.serverPORT = configuration["serverPORT"]
        self.silenceHOST = configuration["silenceHOST"]
        self.silencePORT = configuration["silencePORT"]
        self.bridgeMode = configuration["bridgeMode"]

        self.keepAliveInterval = configuration["keepAliveInterval"]       
        self.connectionCount = 0
        self.ACKresponse = b'\x06'

        self.BMScellVoltage_pooling_interval = configuration["BMScellVoltage_pooling_interval"]

        self.parser = MessageParser()
        
        if configuration["SaveToDB"]:
            log.info("Starting DBService")
            self.dbService = DBService()
            self.dbService.start()

        self.commandService = CommandService()
        self.commandService.start()

    def stop(self):
        self._stop_event.set()

        if self.dbService:
            self.dbService.stop()
            
    def run(self):

        def listenerClient(scooterSocket):

            try:
                #FATTA CONNESSIONE VERIFICO IL LOGIN
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

                #verifico IMEI corretto
                if retrievedIMEI == self.IMEI:
                    self.connectionCount = self.connectionCount +1
                    if self.bridgeMode:
                        log.info("bridge mode, try to connect to Silence server")
                        silenceClientSocket.connect((self.silenceHOST, self.silencePORT))  # provo a connettermi al server silence.
                        log.info("connected to silence")

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
                                scooterSocket.send(data)    #giro messaggio allo scooter
                                log.info("login correct sending ack")
                        else:
                            raise Exception("silence didn't confirm login, closing")

                    else:
                        scooterSocket.send(self.ACKresponse)

                else:           #se imei errato creo eccezione e chiudo tutto
                    log.error("no my IMEI")
                    raise Exception("Wrong login")

                #wait for old socket to close.
                wait_counter = 0
                while self.connectionCount > 1:
                    wait_counter += 1
                    if wait_counter > 100:
                        raise Exception("two valid login at the same time detected")
                    time.sleep(0.1)

                last_keep_alive_sent = time.time()
                last_BMS_pooling_time = 0
                while 1:    #ciclo comunicazione con Scooter
                    #vedo se lo scooter ha dati da inviare
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

                    # vedo se ho comandi da inviare allo scooter
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
                        last_BMS_pooling_time = time.time()

                    if self.keepAliveInterval > 0 and time.time() - last_keep_alive_sent > self.keepAliveInterval:
                        scooterSocket.send(self.ACKresponse)
                        log.info("sent ack to scooter") 
                        last_keep_alive_sent = time.time()                       

                    if self.bridgeMode:
                        #vedo se silence ha qualcosa da inviare allo scooter
                        dataFromSilence,crashedFlag = self._telegramReceiver(silenceClientSocket, 0.2, 0.2, "S")
                        if crashedFlag == 1:
                            raise Exception("crashed socket")
                        
                        elif len(dataFromSilence) > 0:     
                            for message in dataFromSilence:
                                data = message["data"]
                                protocol = message["protocol"]
                                if data.decode()[:5] == "$RCAN": #non inviare a silence i magheggi che facciamo sul CAN
                                    continue
                                scooterSocket.send(data)
                                log.info(f"sent to scooter the data from silence {data}")

                        #else:
                        #    log.info("no data from silence")

                    if self.connectionCount > 1:     #se è stato aperto un nuovo socket chiudo questo thread.
                        raise Exception("two connection detected, closing the old one")

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
                    
                self.connectionCount = self.connectionCount - 1
                log.info("closing thread, connected clients: "+str(self.connectionCount))

        #-------------------------------------ASCOLTO CONNESSIONE IN ARRIVO---------------------------------------
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)   #configurazione socket
        s.bind(("", self.serverPORT))    #forza ricezione connessioni solo da host
        s.settimeout(60)

        log.info(f"socket created on port {self.serverPORT}")
        
        while (1):  #loop per ricreare socket di ascolto in caso di disconnessione
            
            try:    #tentativo per la connessione
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
                data += byte
                if len(data) == oldDataLen:     #se il bytearray non è incrementato di lunghezza significa che è crashato il socket.
                    if firstFrame:
                        if len(data) > 0:
                            _messageReceived(messages,data,protocol)
                        return messages,0
                    else:
                        log.error("crashed socket 2")
                        raise Exception("crashed socket 2")

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
                    data += byte
                    if protocol == "Astra" and int(data[-1]) == 10 and int(data[-2]) == 13: #check for end of message started with $
                        _messageReceived(messages,data,protocol)
                        break
                    elif protocol == "Z" and len(data) >= 3 and int.from_bytes(data[1:3], byteorder='big') == len(data):    #check for end of message started with Z
                        _messageReceived(messages,data,protocol)
                        break

                    oldDataLen = len(data)

            except (socket.timeout, ConnectionError, TimeoutError) as e:
                if len(data) > 0:
                    _messageReceived(messages,data,protocol)
                return messages,0

            except Exception:
                log.exception("_telegramReceiver exception")
                return messages,1   #se non cè stata eccezione timeout significa che il socket si era bloccato.
