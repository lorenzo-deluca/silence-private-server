"""Unit tests for the audit fixes in services/.

These tests validate:
- CommandService logs no longer reference an undefined `imei` name
  (NameError killed the scooter socket thread on every command from HA)
- SilenceServerService._telegramReceiver treats recv() == b'' as EOF
  instead of busy-looping at 100% CPU on a half-closed socket
- MQTTService.start() no longer raises when the broker is unreachable
  (paho connect_async + loop retries in the background instead)

No network, no broker: sockets are faked, MQTT connects asynchronously.
"""
import socket
import time

import pytest

from helpers.command import Command
from services.CommandService import CommandService
from services.SilenceServerService import SilenceServerService
from services.MQTTService import MQTTService


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class FakeSocket:
    """Scripted socket: recv() returns/raises each item once, then b'' (EOF).

    Fails the test if recv() is called an absurd number of times, which is
    exactly what the pre-fix busy-loop did on a closed socket.
    """

    def __init__(self, script, max_recv_calls=500):
        self.script = list(script)
        self.recv_calls = 0
        self.max_recv_calls = max_recv_calls

    def settimeout(self, timeout):
        pass

    def recv(self, bufsize):
        self.recv_calls += 1
        if self.recv_calls > self.max_recv_calls:
            raise AssertionError("busy-loop detected: recv() called too many times")
        if self.script:
            item = self.script.pop(0)
            if isinstance(item, BaseException):
                raise item
            return item
        return b''


def _receive(fake_socket, first_frame=True, receiver="A"):
    # _telegramReceiver does not use `self`, call it unbound
    return SilenceServerService._telegramReceiver(
        None, fake_socket, 0.2, 0.2, receiver, first_frame)


COMMAND_CONFIG = {"command": "$POLL\r\n", "maxretry": 3, "timeout": 60}


@pytest.fixture
def command_service():
    """CommandService with a minimal commands definition, no thread started."""
    service = CommandService({}, "868000000000000")
    service.commands_definition = {"POLL": COMMAND_CONFIG}
    return service


# ---------------------------------------------------------------------------
# Bug 2: _telegramReceiver EOF handling (busy-loop on disconnect)
# ---------------------------------------------------------------------------

def test_telegram_receiver_eof_on_idle_socket_flags_crash():
    # Peer closed the connection: recv() returns b'' immediately.
    # Pre-fix this returned crashedFlag=0 and the caller spun forever.
    messages, crashed = _receive(FakeSocket([]))
    assert crashed == 1
    assert messages == []


def test_telegram_receiver_eof_mid_frame_does_not_busy_loop():
    # FIN in the middle of an Astra frame: the inner recv() loop must
    # detect b'' instead of re-checking the same bytes forever.
    messages, crashed = _receive(FakeSocket([b'$', b'G', b'P']))
    assert crashed == 1


def test_telegram_receiver_eof_mid_frame_Z_protocol():
    # Z frame announcing 10 bytes but connection dies after 4.
    messages, crashed = _receive(FakeSocket([b'Z', b'\x00', b'\x0a', b'\x01']))
    assert crashed == 1


def test_telegram_receiver_connection_reset_flags_crash():
    # RST mid-communication: must be a crash (1), not "no data" (0),
    # otherwise the caller loops calling recv() on a dead socket.
    messages, crashed = _receive(FakeSocket([ConnectionResetError(104, "reset")]))
    assert crashed == 1


def test_telegram_receiver_timeout_still_returns_no_crash():
    # A plain timeout is the normal "nothing to read" case: flag stays 0.
    messages, crashed = _receive(FakeSocket([socket.timeout()]))
    assert crashed == 0
    assert messages == []


def test_telegram_receiver_complete_frame_then_eof():
    # A full Astra frame is delivered, then EOF is reported on the next read.
    frame = [b'$', b'A', b'\r', b'\n']
    messages, crashed = _receive(FakeSocket(frame))
    assert crashed == 1
    assert len(messages) == 1
    assert messages[0]["protocol"] == "Astra"
    assert bytes(messages[0]["data"]) == b'$A\r\n'


def test_telegram_receiver_partial_frame_from_scooter_is_dropped():
    # NEW behaviour: a truncated Astra frame (GSM cut / flaky CAN bus) is
    # never delivered to the parser — misaligned split() used to produce
    # absurd values (odo=980M). A round with ONLY garbage closes the socket.
    messages, crashed = _receive(FakeSocket([b'$', b'A', socket.timeout()]))
    assert crashed == 1
    assert len(messages) == 0


def test_telegram_receiver_partial_frame_from_silence_bridge_kept():
    # Legacy behaviour preserved on the Silence-official side (bridge mode).
    messages, crashed = _receive(FakeSocket([b'$', b'A', socket.timeout()]),
                                 receiver="S")
    assert crashed == 0
    assert len(messages) == 1
    assert bytes(messages[0]["data"]) == b'$A'


def test_telegram_receiver_scanner_junk_dropped_and_connection_closed():
    # Internet scanners hitting the exposed port send HTTP bytes; they must
    # never reach the parser/DB and the connection must be closed at once.
    junk = [bytes([c]) for c in b'GET / HTTP/1.1'] + [socket.timeout()]
    messages, crashed = _receive(FakeSocket(junk))
    assert crashed == 1
    assert len(messages) == 0


def test_telegram_receiver_valid_frame_then_junk_keeps_connection():
    # A round that contains at least one VALID frame keeps the connection
    # alive even if trailing garbage is dropped (flaky scooter mid-ride).
    seq = ([bytes([c]) for c in b'$RCAN,ER' + b'\r' + b'\n']
           + [bytes([c]) for c in b'GARBAGE'] + [socket.timeout()])
    messages, crashed = _receive(FakeSocket(seq))
    assert crashed == 0
    assert len(messages) == 1
    assert bytes(messages[0]["data"]) == b'$RCAN,ER' + b'\r' + b'\n'


# ---------------------------------------------------------------------------
# Bug 1: CommandService NameError on undefined `imei`
# ---------------------------------------------------------------------------

def test_command_received_queues_without_nameerror(command_service):
    # Pre-fix the log f-string raised NameError, swallowed and re-raised
    # as "Command not configurated" for every valid command.
    command_service.command_received("POLL", "")
    assert command_service.command_queue.qsize() == 1


def test_command_executed_without_nameerror(command_service):
    command_service.command_received("POLL", "")
    command = command_service.get_next_command()
    # Pre-fix this raised NameError in the scooter socket thread.
    command_service.command_executed(command, "Ok".encode())
    assert command_service.command_queue.qsize() == 0


def test_command_failed_requeues_for_retry(command_service):
    command_service.command_received("POLL", "")
    command = command_service.get_next_command()
    command_service.command_failed(command)
    # maxretry=3, first failure: command must be back in the queue.
    assert command_service.command_queue.qsize() == 1


def test_command_failed_retry_limit_without_nameerror(command_service):
    command_service.commands_definition = {
        "POLL": {"command": "$POLL\r\n", "maxretry": 1, "timeout": 60}}
    command_service.command_received("POLL", "")
    command = command_service.get_next_command()
    command_service.command_failed(command)
    # maxretry=1: retry limit reached, not requeued.
    assert command_service.command_queue.qsize() == 0


def test_get_next_command_timeout_without_nameerror(command_service):
    command_service.command_received("POLL", "")
    expired = command_service.command_queue.queue[0]
    expired.TSInserted = time.time() - 999
    # Pre-fix the timeout log raised NameError.
    command = command_service.get_next_command()
    assert command is expired


def test_cleanup_queue_timeout_without_nameerror(command_service):
    command_service.command_received("POLL", "")
    command_service.command_queue.queue[0].TSInserted = time.time() - 999
    command_service.cleanup_queue()
    assert command_service.command_queue.qsize() == 0


# ---------------------------------------------------------------------------
# Bug 3: MQTTService must not die when the broker is unreachable
# ---------------------------------------------------------------------------

def test_mqtt_start_does_not_raise_with_broker_down():
    configuration = {
        "MQTTbroker": "127.0.0.1",
        "MQTTport": 1,          # nothing listens here
        "MQTTuser": "user",
        "MQTTpass": "pass",
        "TopicPrefix": "silence-scooter",
    }
    service = MQTTService(configuration, "868000000000000")
    try:
        # Pre-fix a synchronous connect() raised and the caller left the
        # server as a zombie (TCP ACKs, no MQTT publish, no retry).
        service.start()
        assert service.client.on_connect_fail is not None
        assert service.client.on_disconnect is not None
    finally:
        service.stop()
