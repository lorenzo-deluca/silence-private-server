"""Unit tests for helpers.messageParser.MessageParser.

These tests validate:
- Normal Z frame parsing (odo, speed, status, battery, ...)
- Bundled Z frame debundling (sub-frame extraction)
- Corrupt Z frame filtering (garbage odometer values)
- Extended CAN ($RCAN) parsing for drive mode, BMS, motor, bus voltage, NTC
- "Scooter off" reset of extended CAN fields
- IMEI / connection-independent behaviour (no network calls)

Real raw frames captured from a SEAT Mo 125 with the Astra AT402 v7.0.61.35
telematics module are used where possible.
"""
import pytest
from unittest.mock import patch

import helpers.messageParser as mp
from helpers.messageParser import MessageParser


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def parser():
    """Fresh parser for each test."""
    return MessageParser()


# ---------------------------------------------------------------------------
# Basic parser behaviour
# ---------------------------------------------------------------------------

def test_parser_initializes_with_scooter_off(parser):
    assert parser.scooter_off is True
    assert parser.off_statuses == [0, 1, 5]


def test_parser_loads_all_required_configs(parser):
    # Must load Z_protocol and RCAN configs plus the parameters dict
    assert "odo" in parser.message_decode
    assert "Cell1Voltage" in parser.RCAN_message_configuration
    assert "odo" in parser.parameters
    assert "status" in parser.parameters


def test_parser_declares_extended_can_keys(parser):
    # Our fix relies on a known list of extended-CAN fields
    for key in ["driveMode", "motorRPM", "motorPower", "busVoltage",
                "bmsCurrent", "batteryNTC1", "batteryNTC2", "batteryNTC3"]:
        assert key in parser._extended_can_keys


def test_parser_handles_empty_data(parser):
    # Should silently ignore empty bytearrays, never raise.
    parser.parse_message_from_scooter_protocol_Z(b"")
    parser.parse_message_from_scooter_protocol_astra(b"")
    # No state changes expected
    assert parser.scooter_off is True


# ---------------------------------------------------------------------------
# $RCAN parsing (extended CAN sensors)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "rcan_id,bits,expected_mode",
    [
        ("280", 0x00, "OFF"),    # bits 4-5 = 00
        ("280", 0x10, "ECO"),    # bits 4-5 = 01
        ("280", 0x20, "SPORT"),  # bits 4-5 = 10
        ("280", 0x30, "CITY"),   # bits 4-5 = 11
    ],
)
def test_rcan_280_drive_mode_mapping(parser, rcan_id, bits, expected_mode):
    # byte0 encodes driveReady (bit 0), sidestandDown (bit 3), drive mode (bits 4-5)
    # parts layout: $RCAN,{ID},{len},{b0},{b1}
    frame = f"$RCAN,{rcan_id},2,{bits:02X},00\r\n"
    parser._parse_extended_can(frame)
    assert parser.parameters["driveMode"]["value"] == expected_mode


def test_rcan_280_drive_ready_and_sidestand(parser):
    # byte0 = 0b00001001 = 0x09: driveReady=1 (bit 0), sidestandDown=1 (bit 3)
    parser._parse_extended_can("$RCAN,280,2,09,01\r\n")
    assert parser.parameters["driveReady"]["value"] == 1
    assert parser.parameters["sidestandDown"]["value"] == 1
    assert parser.parameters["warningLights"]["value"] == 1  # byte1 bit 0 = 1


def test_rcan_300_range_by_mode(parser):
    # 0x300 - Range by drive mode at byte1 (parts[4])
    parser._parse_extended_can("$RCAN,300,2,00,2D\r\n")  # 0x2D = 45 km
    assert parser.parameters["rangeByMode"]["value"] == 45


def test_rcan_182_bms_flags(parser):
    parser._parse_extended_can("$RCAN,182,1,0F\r\n")
    assert parser.parameters["bmsFlags"]["value"] == 0x0F


def test_rcan_181_bms_current_positive(parser):
    # Bytes 6-7 little-endian, signed, /10 = amps
    # 0x0064 = 100 -> 10.0 A
    parser._parse_extended_can("$RCAN,181,8,00,00,00,00,00,00,64,00,OK")
    assert parser.parameters["bmsCurrent"]["value"] == 10.0


def test_rcan_181_bms_current_negative(parser):
    # 0xFF9C = -100 (signed 16-bit) -> -10.0 A
    parser._parse_extended_can("$RCAN,181,8,00,00,00,00,00,00,9C,FF,OK")
    assert parser.parameters["bmsCurrent"]["value"] == -10.0


def test_rcan_189_battery_ntc_three_probes(parser):
    # Bytes 2-7 encode 3 NTC probes (2 bytes each, LE, /100 = celsius, rounded to 1 decimal)
    # 0x09C4=2500 -> 25.0C, 0x0A28=2600 -> 26.0C, 0x0ABE=2750 -> 27.5C
    parser._parse_extended_can("$RCAN,189,8,00,00,C4,09,28,0A,BE,0A,OK")
    assert parser.parameters["batteryNTC1"]["value"] == 25.0
    assert parser.parameters["batteryNTC2"]["value"] == 26.0
    assert parser.parameters["batteryNTC3"]["value"] == 27.5


def test_rcan_391_motor_rpm(parser):
    # Bytes 4-5 LE unsigned -> RPM
    # 0x0BB8 = 3000
    parser._parse_extended_can("$RCAN,391,6,00,00,00,00,B8,0B,OK")
    assert parser.parameters["motorRPM"]["value"] == 3000


def test_rcan_381_motor_power_positive(parser):
    # Bytes 2-3 LE signed -> motor power
    # 0x01F4 = 500
    parser._parse_extended_can("$RCAN,381,4,00,00,F4,01,OK")
    assert parser.parameters["motorPower"]["value"] == 500


def test_rcan_381_motor_power_negative_regen(parser):
    # Negative power = regeneration
    # 0xFF38 = -200
    parser._parse_extended_can("$RCAN,381,4,00,00,38,FF,OK")
    assert parser.parameters["motorPower"]["value"] == -200


def test_rcan_371_bus_voltage(parser):
    # Bytes 6-7 LE unsigned, /10 = volts
    # 0x0320 = 800 -> 80.0 V
    parser._parse_extended_can("$RCAN,371,8,00,00,00,00,00,00,20,03,OK")
    assert parser.parameters["busVoltage"]["value"] == 80.0


def test_rcan_unknown_id_is_ignored(parser):
    # Should not raise on unknown RCAN IDs
    before = dict(parser.parameters)
    parser._parse_extended_can("$RCAN,999,2,00,00\r\n")
    # No values should have changed
    for k in parser._extended_can_keys:
        assert parser.parameters[k]["value"] == before[k]["value"]


def test_rcan_malformed_frame_does_not_raise(parser):
    # Too few fields, non-hex data, etc. should be caught
    parser._parse_extended_can("$RCAN,181,not-enough-fields")
    parser._parse_extended_can("$RCAN,181,8,ZZ,00,00,00,00,00,00,00,OK")
    parser._parse_extended_can("not-an-rcan-frame")
    # No assertion needed — we just verify no exception escapes


# ---------------------------------------------------------------------------
# $RCAN 185-188 cell voltages via parse_message_from_scooter_protocol_astra
# ---------------------------------------------------------------------------

def test_astra_rcan_185_cell_voltages_via_public_api(parser):
    # $RCAN,185 carries cell voltages 1-4. Layout (0-indexed):
    #   positions[0] = "$RCAN"
    #   positions[1] = "185"
    #   positions[2] = "8"       (length)
    #   positions[3] = b0_hi     Cell1 high byte
    #   positions[4] = b0_lo     Cell1 low byte
    #   positions[5..10] = Cells 2..4
    # combined = positions[byte_pos[1]] + positions[byte_pos[0]]
    # For Cell1 (byte_pos [3,4]): positions[4] + positions[3]
    # So "0E" + "0D" = "0E0D" = 3597 mV
    parser.parse_message_from_scooter_protocol_astra(
        b"$RCAN,185,8,0D,0E,0E,0D,0C,0E,0F,0D,OK\r\n"
    )
    assert parser.parameters["Cell1Voltage"]["value"] == 0x0E0D   # 3597
    assert parser.parameters["Cell2Voltage"]["value"] == 0x0D0E   # 3342
    assert parser.parameters["Cell3Voltage"]["value"] == 0x0E0C   # 3596
    assert parser.parameters["Cell4Voltage"]["value"] == 0x0D0F   # 3343


def test_astra_rcan_186_cell_voltages_via_public_api(parser):
    # Cells 5-8 on $RCAN,186 — same encoding pattern
    parser.parse_message_from_scooter_protocol_astra(
        b"$RCAN,186,8,10,0E,11,0E,12,0E,13,0E,OK\r\n"
    )
    assert parser.parameters["Cell5Voltage"]["value"] == 0x0E10
    assert parser.parameters["Cell6Voltage"]["value"] == 0x0E11
    assert parser.parameters["Cell7Voltage"]["value"] == 0x0E12
    assert parser.parameters["Cell8Voltage"]["value"] == 0x0E13


def test_astra_also_triggers_extended_can_parsing(parser):
    # parse_message_from_scooter_protocol_astra also calls _parse_extended_can
    # so sending an 0x280 frame via the public API must decode the drive mode.
    parser.parse_message_from_scooter_protocol_astra(b"$RCAN,280,2,21,00,OK\r\n")
    assert parser.parameters["driveMode"]["value"] == "SPORT"


def test_astra_non_rcan_frame_does_not_raise(parser):
    # The $ASTRA login frame and other non-RCAN payloads should be accepted
    # silently (no field matches any header, no exception raised).
    parser.parse_message_from_scooter_protocol_astra(
        b"$ASTRA;AT402;860873043967941;;7.0.61.35;Z;0\r\n"
    )


# ---------------------------------------------------------------------------
# "Scooter off" reset of extended CAN fields (our 2026-04-22 fix)
# ---------------------------------------------------------------------------

def test_extended_can_fields_reset_when_scooter_off(parser):
    # Simulate the scooter having just been ridden:
    parser.parameters["motorRPM"]["value"] = 3000
    parser.parameters["motorPower"]["value"] = 1500
    parser.parameters["driveMode"]["value"] = "SPORT"
    parser.parameters["busVoltage"]["value"] = 82.1
    parser.parameters["bmsCurrent"]["value"] = -30.5
    parser.parameters["batteryNTC1"]["value"] = 25.0
    parser.scooter_off = False

    # Now the scooter shuts down — craft a minimal 94-byte Z frame with
    # status=0 at byte 82. Everything else can be zero, we only care
    # about scooter_off transitioning to True and the reset logic firing.
    frame = bytearray(94)
    frame[0] = 0x5A          # Z marker
    frame[1] = 0x00          # len hi
    frame[2] = 0x5E          # len lo (94)
    frame[3] = 0x01          # sub-count
    frame[82] = 0            # status = 0 (off)

    parser.parse_message_from_scooter_protocol_Z(bytes(frame))

    assert parser.scooter_off is True
    # Extended CAN fields should be reset to "None"
    for key in parser._extended_can_keys:
        assert parser.parameters[key]["value"] == "None", (
            f"{key} not reset on scooter-off"
        )


def test_extended_can_fields_kept_when_scooter_on(parser):
    # When scooter is ON, extended CAN values must NOT be reset — they
    # are the live RPM/power/etc. that we want to publish.
    parser.parameters["motorRPM"]["value"] = 3000
    parser.parameters["driveMode"]["value"] = "SPORT"
    parser.scooter_off = True  # will flip to False when we send status=4

    frame = bytearray(94)
    frame[0] = 0x5A
    frame[1] = 0x00
    frame[2] = 0x5E
    frame[3] = 0x01
    frame[82] = 4            # status = 4 (moving)

    parser.parse_message_from_scooter_protocol_Z(bytes(frame))

    assert parser.scooter_off is False
    # Should not be reset
    assert parser.parameters["motorRPM"]["value"] == 3000
    assert parser.parameters["driveMode"]["value"] == "SPORT"


# ---------------------------------------------------------------------------
# Bundled Z frame debundling (our 2026-04-14 fix)
# ---------------------------------------------------------------------------

def test_normal_z_frame_not_touched_by_debundler(parser):
    # A 94-byte frame is the normal single-record case. The debundler
    # should not touch it.
    frame = bytearray(94)
    frame[0] = 0x5A
    frame[1] = 0x00
    frame[2] = 0x5E  # 94
    frame[3] = 0x01  # 1 sub-record
    frame[82] = 4    # status
    parser.parse_message_from_scooter_protocol_Z(bytes(frame))
    # Status must have been read from byte 82
    assert parser.parameters["status"]["value"] == 4


def _bundle(sub_count, status=4, odo=None, sub_size=88, speed=None):
    """Build a bundled Z frame of `sub_count` records.

    `speed` (byte 63 of a standalone frame -> offset 59 in a sub) lets two
    bundles with the same status and odometer carry different payloads, so
    they are not swallowed by the digest dedup before reaching the guard.
    """
    total_len = 4 + sub_count * sub_size + 2
    frame = bytearray(total_len)
    frame[0] = 0x5A
    frame[1] = (total_len >> 8) & 0xFF
    frame[2] = total_len & 0xFF
    frame[3] = sub_count
    for i in range(sub_count):
        base = 4 + i * sub_size
        # status lives at byte 82 of a standalone frame -> offset 78 in a sub
        frame[base + 78] = 3 if i < sub_count - 1 else status
        if odo is not None:
            # odo occupies bytes 83-86 of a standalone frame -> 79-82 in a sub
            frame[base + 79: base + 83] = int(odo).to_bytes(4, "big")
        if speed is not None:
            frame[base + 59] = int(speed) & 0xFF
    return bytes(frame)


@pytest.mark.parametrize("sub_count", [2, 3, 5, 7, 11])
def test_bundled_frame_with_advancing_odo_keeps_motion(parser, sub_count):
    # A bundle is a BACKLOG. While the odometer keeps advancing the scooter
    # really is riding (the module is just late), so the motion state of the
    # most recent record must be preserved.
    parser.parse_message_from_scooter_protocol_Z(_bundle(sub_count, odo=16000))
    parser.parse_message_from_scooter_protocol_Z(_bundle(sub_count, odo=16005))
    assert parser.parameters["status"]["value"] == 4


@pytest.mark.parametrize("sub_count", [2, 3, 11])
def test_bundled_frame_with_frozen_odo_reports_stopped(parser, sub_count, monkeypatch):
    """Regression test for the 2026-07-26 phantom trips.

    A parked scooter whose module re-sends its pending backlog for hours
    (odometer frozen) must NOT be reported as riding: each replay used to
    publish status=4/speed=82 and opened a trip in Home Assistant.
    """
    clock = [1000.0]
    monkeypatch.setattr(mp, "_monotonic", lambda: clock[0])
    parser.parse_message_from_scooter_protocol_Z(_bundle(sub_count, odo=16681))
    # same odometer, different payloads -> not caught by the dedup; the
    # odometer frozen for longer than the grace period proves the readings
    # are stale (the module alternated between pending bundles all night)
    clock[0] += 120
    parser.parse_message_from_scooter_protocol_Z(_bundle(sub_count, status=4, odo=16681, speed=82))
    assert parser.parameters["status"]["value"] == 4      # still within the grace period
    clock[0] += mp.FROZEN_ODO_GRACE_SECONDS + 60
    parser.parse_message_from_scooter_protocol_Z(_bundle(sub_count, status=4, odo=16681, speed=81))
    assert parser.parameters["status"]["value"] == 0
    assert parser.parameters["speed"]["value"] == 0


def test_frozen_odo_within_grace_keeps_motion(parser, monkeypatch):
    """Regression test for the 2026-09-14 commute.

    On a degraded link the module bundles everything (974-byte frames every
    few seconds). At a red light two or three consecutive bundles carry the
    same 1 km-resolution odometer while the scooter is genuinely riding
    (status=4 in every sub-record). Forcing status=0 on the first frozen
    bundle turned every red light into a pause and the trip into
    '4 km in 2 min at 120 km/h'."""
    clock = [5000.0]
    monkeypatch.setattr(mp, "_monotonic", lambda: clock[0])
    parser.parse_message_from_scooter_protocol_Z(_bundle(11, odo=17219, speed=54))
    for step, speed in ((6, 30), (3, 0), (40, 0), (60, 12)):   # same km, red light
        clock[0] += step
        parser.parse_message_from_scooter_protocol_Z(_bundle(11, status=4, odo=17219, speed=speed))
        assert parser.parameters["status"]["value"] == 4
        assert parser.scooter_off is False


def test_frozen_clock_resets_when_odo_advances(parser, monkeypatch):
    clock = [9000.0]
    monkeypatch.setattr(mp, "_monotonic", lambda: clock[0])
    parser.parse_message_from_scooter_protocol_Z(_bundle(3, odo=100, speed=40))
    clock[0] += mp.FROZEN_ODO_GRACE_SECONDS - 30      # frozen, still in grace
    parser.parse_message_from_scooter_protocol_Z(_bundle(3, status=4, odo=100, speed=41))
    clock[0] += 10
    parser.parse_message_from_scooter_protocol_Z(_bundle(3, status=4, odo=101, speed=42))   # advances -> reset
    clock[0] += mp.FROZEN_ODO_GRACE_SECONDS - 30      # frozen again but the clock restarted
    parser.parse_message_from_scooter_protocol_Z(_bundle(3, status=4, odo=101, speed=43))
    assert parser.parameters["status"]["value"] == 4


def test_two_record_bundle_is_detected(parser):
    """182-byte dual-record bundle (upstream v2026.9.9 regression case).

    The old `len > 200` check let it through as a normal frame: the decode
    config knows no 182-byte layout, so the packet was silently dropped and
    its kilometres lost. Detection now relies on the table of valid
    single-frame lengths."""
    frame = _bundle(2, odo=16000)
    assert len(frame) == 182
    parser.parse_message_from_scooter_protocol_Z(frame)
    parser.parse_message_from_scooter_protocol_Z(_bundle(2, odo=16005))
    assert parser.parameters["odo"]["value"] == 16005.0
    assert parser.parameters["status"]["value"] == 4


def test_bundled_frame_resent_is_ignored(parser):
    """The module re-sends an unacknowledged bundle every few minutes."""
    frame = _bundle(11, odo=16700)
    parser.parse_message_from_scooter_protocol_Z(frame)
    calls = []
    with patch.object(mp.pub, "sendMessage", side_effect=lambda *a, **k: calls.append(k)):
        parser.parse_message_from_scooter_protocol_Z(frame)
        parser.parse_message_from_scooter_protocol_Z(frame)
    assert calls == []


def test_malformed_bundle_is_dropped(parser):
    """Payload not a multiple of the sub-frame size: slicing it would emit
    values straddling two records (odo=-1, soc=-23 seen in production)."""
    frame = bytearray(_bundle(11, odo=16000))
    frame = frame[:-5]                      # truncate: payload no longer 11x88
    frame[1] = (len(frame) >> 8) & 0xFF
    frame[2] = len(frame) & 0xFF
    parser.parameters["odo"]["value"] = 16000.0
    calls = []
    with patch.object(mp.pub, "sendMessage", side_effect=lambda *a, **k: calls.append(k)):
        parser.parse_message_from_scooter_protocol_Z(bytes(frame))
    assert calls == []


def test_bundled_frame_with_sub_count_one_is_not_debundled(parser):
    # Edge case: a "bundled" frame with sub_count=1 should bypass the
    # debundling branch (the guard `if sub_count > 1` at messageParser.py).
    # We build a 250-byte frame with sub_count=1 — the debundler should
    # treat it as a normal frame. The Z-protocol config doesn't know how
    # to parse a 250-byte frame (expected lengths are 94/99/200), so no
    # fields will be extracted, but the parser must not crash.
    total_len = 250
    frame = bytearray(total_len)
    frame[0] = 0x5A
    frame[1] = (total_len >> 8) & 0xFF
    frame[2] = total_len & 0xFF
    frame[3] = 1  # sub_count = 1

    # Must not raise
    parser.parse_message_from_scooter_protocol_Z(bytes(frame))


def test_bundled_frame_degenerate_sub_count_does_not_crash(parser):
    # Attacker / malformed input: sub_count too high for the payload,
    # leading to `sub_size = (len - 4 - 2) // sub_count = 0`. The guard
    # `if sub_size > 0` must prevent any slice manipulation.
    total_len = 250  # not a known single-frame length -> debundling branch
    frame = bytearray(total_len)
    frame[0] = 0x5A
    frame[1] = (total_len >> 8) & 0xFF
    frame[2] = total_len & 0xFF
    frame[3] = 250  # way too many "sub-records"

    # Must not raise even though sub_size evaluates to 0
    parser.parse_message_from_scooter_protocol_Z(bytes(frame))


# ---------------------------------------------------------------------------
# Corrupt Z frame filtering (our 2026-04-14 fix)
# ---------------------------------------------------------------------------

def test_corrupt_odo_value_is_rejected(parser):
    # An odo of 0x33FFFFFF ~ 870M is a known shutdown-corruption signature.
    # We first seed a good value to see it NOT be overwritten.
    parser.parameters["odo"]["value"] = 15026.0
    parser.scooter_off = False  # Must be off->on so numeric fields are parsed

    # Build a frame with odo at bytes 83-86 = 0x33FFFFFF
    frame = bytearray(94)
    frame[0] = 0x5A
    frame[1] = 0x00
    frame[2] = 0x5E
    frame[3] = 0x01
    frame[82] = 4  # status=4 so scooter_off flips to False
    # odo bytes 83-86 big-endian = 0x33FFFFFF
    frame[83] = 0x33
    frame[84] = 0xFF
    frame[85] = 0xFF
    frame[86] = 0xFF

    parser.parse_message_from_scooter_protocol_Z(bytes(frame))

    # The corrupt value is rejected AND the cache is rolled back to the
    # previous good value, so it can never leak through a later publish
    # triggered by another frame type.
    assert parser.parameters["odo"]["value"] == 15026.0


def test_corrupt_odo_skips_publish(parser, monkeypatch):
    """Strong version: verify pub.sendMessage is NOT called on corrupt frame."""
    calls = []
    monkeypatch.setattr(mp.pub, "sendMessage", lambda *a, **kw: calls.append((a, kw)))

    parser.scooter_off = False

    frame = bytearray(94)
    frame[0] = 0x5A
    frame[1] = 0x00
    frame[2] = 0x5E
    frame[3] = 0x01
    frame[82] = 4
    frame[83] = 0x33
    frame[84] = 0xFF
    frame[85] = 0xFF
    frame[86] = 0xFF

    parser.parse_message_from_scooter_protocol_Z(bytes(frame))

    # No publish must have occurred because the frame was rejected
    status_publishes = [c for c in calls if c[1].get("scooter_status") is not None
                        or (c[0] and c[0][0] == mp.TOPIC_SCOOTER_STATUS)]
    assert status_publishes == [], (
        f"Corrupt frame should not trigger a publish, got: {calls}"
    )


def test_valid_odo_does_publish(parser, monkeypatch):
    """Positive control: a frame with a plausible odo DOES publish."""
    calls = []
    monkeypatch.setattr(mp.pub, "sendMessage", lambda *a, **kw: calls.append((a, kw)))

    parser.scooter_off = False

    frame = bytearray(94)
    frame[0] = 0x5A
    frame[1] = 0x00
    frame[2] = 0x5E
    frame[3] = 0x01
    frame[82] = 4  # status=4
    # odo = 15026 = 0x00003AB2
    frame[83] = 0x00
    frame[84] = 0x00
    frame[85] = 0x3A
    frame[86] = 0xB2

    parser.parse_message_from_scooter_protocol_Z(bytes(frame))

    assert len(calls) >= 1, "A valid frame must trigger at least one publish"


# ---------------------------------------------------------------------------
# Status transitions drive scooter_off flag
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("status,expected_off", [
    (0, True),   # off
    (1, True),   # lock
    (2, False),  # startup
    (3, False),  # ready/idle (moving trip active)
    (4, False),  # riding
    (5, True),   # shutdown
])
def test_scooter_off_flag_follows_status(parser, status, expected_off):
    parser.scooter_off = not expected_off  # start in opposite state

    frame = bytearray(94)
    frame[0] = 0x5A
    frame[1] = 0x00
    frame[2] = 0x5E
    frame[3] = 0x01
    frame[82] = status

    parser.parse_message_from_scooter_protocol_Z(bytes(frame))
    assert parser.scooter_off is expected_off


def test_get_scooter_off_status_getter(parser):
    assert parser.get_scooter_off_status() is True
    parser.scooter_off = False
    assert parser.get_scooter_off_status() is False


# ---------------------------------------------------------------------------
# Ghost-values fix: nothing decoded => nothing republished
# ---------------------------------------------------------------------------

def _capture_publishes(monkeypatch):
    calls = []
    monkeypatch.setattr(mp.pub, "sendMessage", lambda *a, **kw: calls.append(kw))
    return calls


def test_astra_er_frame_does_not_republish_cache(parser, monkeypatch):
    """$RCAN,ER (CAN bus error) used to republish the ENTIRE stale cache and
    refresh last-update — the source of 'ghost riding' telemetry while the
    scooter sat parked with a faulty contactor."""
    calls = _capture_publishes(monkeypatch)
    parser.parse_message_from_scooter_protocol_astra(bytearray(b'$RCAN,ER\r\n'))
    assert calls == []


def test_astra_heartbeat_does_not_republish_cache(parser, monkeypatch):
    calls = _capture_publishes(monkeypatch)
    parser.parse_message_from_scooter_protocol_astra(
        bytearray(b'$ASTRA;AT402;860000000000000;;7.0.61.35;Z;0\r\n'))
    assert calls == []


def test_astra_valid_cells_publish_only_decoded_keys(parser, monkeypatch):
    calls = _capture_publishes(monkeypatch)
    parser.parse_message_from_scooter_protocol_astra(
        bytearray(b'$RCAN,185,08,5A,0E,5E,0E,64,0E,62,0E,OK\r\n'))
    assert len(calls) == 1
    published = calls[0]["scooter_status"]
    assert set(published.keys()) == {"Cell1Voltage", "Cell2Voltage", "Cell3Voltage", "Cell4Voltage"}
    assert published["Cell1Voltage"]["value"] == 0x0E5A


# ---------------------------------------------------------------------------
# $STMS snapshot frame (SYNC command response)
# ---------------------------------------------------------------------------

STMS_FRAME = (
    b"$STMS,0,82,25,24,556,0,435036987,0,0,330,330,0,109,0,"
    b"1193553,101673,1158504,22,89340760000000000000,+34600000000,"
    b"UCYS1234567890123\r\n"
)


def test_stms_frame_decodes_snapshot_fields(parser):
    parser.parse_message_from_scooter_protocol_astra(STMS_FRAME)
    p = parser.parameters
    assert p["batterySOC"]["value"] == 82
    assert p["batteryTempMax"]["value"] == 25
    assert p["batteryTempMin"]["value"] == 24
    assert p["batteryVoltage"]["value"] == 55.6
    assert p["range"]["value"] == 109
    assert p["chargedEnergy"]["value"] == pytest.approx(331.5425)
    assert p["RegeneratedEnergy"]["value"] == pytest.approx(28.2425)
    assert p["DischargedEnergy"]["value"] == pytest.approx(321.8067, abs=1e-4)
    assert p["ambientTemp"]["value"] == 22
    assert p["VIN"]["value"] == "UCYS1234567890123"


def test_stms_publishes_status(parser, monkeypatch):
    calls = []
    import helpers.messageParser as mp
    monkeypatch.setattr(mp.pub, "sendMessage", lambda *a, **kw: calls.append((a, kw)))
    parser.parse_message_from_scooter_protocol_astra(STMS_FRAME)
    assert len(calls) == 1


def test_stms_leaves_unidentified_fields_untouched(parser):
    # $STMS does not carry odo (field 7 is an unrelated large counter);
    # an odo previously read from Z telemetry must survive a snapshot.
    parser.parameters["odo"]["value"] = 6345.0
    parser.parse_message_from_scooter_protocol_astra(STMS_FRAME)
    assert parser.parameters["odo"]["value"] == 6345.0


def test_stms_rejects_implausible_vin(parser):
    # A truncated/garbled last field must not overwrite a known-good VIN.
    parser.parameters["VIN"]["value"] = "UCYS_KNOWN_GOOD00"
    frame = STMS_FRAME.replace(b"UCYS1234567890123", b"GARBLED")
    parser.parse_message_from_scooter_protocol_astra(frame)
    assert parser.parameters["VIN"]["value"] == "UCYS_KNOWN_GOOD00"


def test_stms_undecodable_frame_does_not_publish(parser, monkeypatch):
    calls = []
    import helpers.messageParser as mp
    monkeypatch.setattr(mp.pub, "sendMessage", lambda *a, **kw: calls.append((a, kw)))
    parser.parse_message_from_scooter_protocol_astra(b"$STMS,,\r\n")
    assert calls == []


def test_stms_publishes_only_decoded_keys(parser, monkeypatch):
    """Same contract as $RCAN: no republication of the whole cache."""
    calls = []
    monkeypatch.setattr(mp.pub, "sendMessage", lambda *a, **kw: calls.append(kw))
    parser.parameters["odo"]["value"] = 6345.0
    parser.parameters["speed"]["value"] = 82
    parser.parse_message_from_scooter_protocol_astra(STMS_FRAME)
    assert len(calls) == 1
    published = calls[0]["scooter_status"]
    assert "batterySOC" in published and "VIN" in published
    assert "odo" not in published and "speed" not in published


def test_stms_does_not_touch_status(parser, monkeypatch):
    """Field 1 semantics are unknown (single parked capture = 0): a SYNC
    requested while riding must not stop the trip in Home Assistant."""
    calls = []
    monkeypatch.setattr(mp.pub, "sendMessage", lambda *a, **kw: calls.append(kw))
    parser.parameters["status"]["value"] = 4
    parser.scooter_off = False
    parser.parse_message_from_scooter_protocol_astra(STMS_FRAME)
    assert parser.parameters["status"]["value"] == 4
    assert parser.scooter_off is False
    assert "status" not in calls[0]["scooter_status"]
