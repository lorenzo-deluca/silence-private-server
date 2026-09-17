from helpers.constants import *
import collections
import hashlib
import json
import logging
import os
import time

from pubsub import pub

log = logging.getLogger(LOGGER_NAME)

# Payload size of a single Z record: a standalone frame is 94 bytes
# (4 header + 88 payload + 2 checksum). Bundled frames pack N such
# payloads back to back.
Z_SUB_SIZE = 88

# A backlogged bundle whose odometer has not advanced is only declared
# "parked" once the odometer has been frozen for this long. The odometer
# has a 1 km resolution: at a red light or in a traffic jam consecutive
# bundles legitimately carry the same value for a couple of minutes
# (2026-09-14: every red light of a real ride was published as status=0,
# splitting the trip into 'pauses' and yielding a 120 km/h average).
# The 2026-07-26 phantom replays, by contrast, stayed frozen for hours.
FROZEN_ODO_GRACE_SECONDS = 300


def _monotonic():
    """Wall-clock source, isolated so tests can drive it."""
    return time.monotonic()


class MessageParser:

    def __init__(self):

        self.scooter_off = True
        self.off_statuses = [0,1,5]

        # Fingerprints of recently seen bundled frames, to drop the module's
        # unacknowledged re-sends (see parse_message_from_scooter_protocol_Z).
        # A bounded deque: the module cycles through its pending bundles for
        # hours, so a single-slot memory is not enough.
        self._seen_bundle_digests = collections.deque(maxlen=64)

        # Set while parsing a bundled (backlogged) frame, so the publish step
        # knows the motion state it carries is historical, not live.
        self._bundle_backlog = False
        # Odometer seen in the previous bundle: a backlog whose odometer no
        # longer advances describes a parked scooter, not a ride.
        self._last_bundle_odo = None
        # When the odometer first stopped advancing across bundles (see
        # FROZEN_ODO_GRACE_SECONDS); None while it is advancing.
        self._odo_frozen_since = None

        # load message parsing configuration
        with open(os.path.join(os.path.dirname(__file__), "Z_protocol_message_decode.json")) as message_configuration:
            self.message_decode = json.load(message_configuration)

        with open("scooter_status_definition.json") as status_definition:
            self.parameters = json.load(status_definition)

        with open(os.path.join(os.path.dirname(__file__), "RCAN_definition.json")) as RCAN_message_configuration:
            self.RCAN_message_configuration = json.load(RCAN_message_configuration)

        # Valid single-frame lengths derived from decode config — used to detect bundled frames.
        self._valid_z_lengths = {
            l
            for p in self.message_decode.values()
            for mt in p["message_type"]
            for l in mt["message_lenght"]
        }

        # Fields populated by extended CAN polling (_parse_extended_can).
        # These are reset to "None" when the scooter is off so consumers
        # don't see stale values (e.g. RPM=1341, driveMode=SPORT while
        # the scooter has been parked for hours).
        self._extended_can_keys = [
            "driveMode", "driveReady", "sidestandDown", "warningLights",
            "rangeByMode", "bmsFlags", "bmsCurrent",
            "batteryNTC1", "batteryNTC2", "batteryNTC3",
            "motorRPM", "motorPower", "busVoltage",
        ]

    def parse_message_from_scooter_protocol_Z(self, data):

        if len(data) > 0:
            log.debug(f"Parse received message protocol Z from scooter: {data}")

            # Remember the previous odo so a corrupt frame can be rolled back
            # instead of leaving the poisoned value in the cache (it would
            # leak on the next publish triggered by any other frame).
            odo_prev = self.parameters.get("odo", {}).get("value")

            # Handle bundled Z frames: when the link degrades, the Astra
            # module queues its readings and sends them as one big frame.
            # Format: Z[len_hi][len_lo][count][sub0][sub1]...[checksum]
            # with sub-frames of Z_SUB_SIZE bytes (a single-record frame is
            # 4 header + 88 payload + 2 checksum = 94 bytes).
            #
            # Three defects fixed here (they caused the 2026-07 "ghost rides"):
            #  1. the module RE-SENDS the same bundle every few minutes until
            #     it is acknowledged. Each replay was parsed as fresh data, so
            #     a scooter parked since 20:27 kept reporting "status=4,
            #     speed=82" all night and opened a trip on every replay.
            #  2. only the LAST sub-frame was kept: the 10 other readings
            #     (speeds, kilometres) were dropped, under-reporting distance.
            #  3. malformed bundles (payload not a multiple of the sub-frame
            #     size) were sliced anyway, publishing values straddling two
            #     records (odo=-1, soc=-23...).
            #
            # Detection: any Z frame whose length is not a known single-frame
            # size (table built from the decode config), instead of the old
            # `len > 200` test that silently dropped 182-byte dual-record
            # bundles (upstream fix, v2026.9.9).
            if data[0] == 0x5A and len(data) >= 4 and len(data) not in self._valid_z_lengths:
                sub_count = data[3]
                if sub_count > 1:
                    payload = data[4:-2]
                    if len(payload) != sub_count * Z_SUB_SIZE:
                        log.warning(
                            "Malformed bundled Z frame: %d payload bytes for %d "
                            "sub-frames (expected %d), dropping",
                            len(payload), sub_count, sub_count * Z_SUB_SIZE,
                        )
                        return

                    # Fingerprint the PAYLOAD and keep a short history: an
                    # unacknowledged bundle is re-sent for hours, and the
                    # module alternates between a handful of pending bundles,
                    # so remembering only the previous one lets them through.
                    digest = hashlib.md5(bytes(payload)).hexdigest()
                    if digest in self._seen_bundle_digests:
                        log.info(
                            "Duplicate bundled Z frame re-sent by the module "
                            "(%d sub-frames), ignoring", sub_count,
                        )
                        return
                    self._seen_bundle_digests.append(digest)

                    # A bundle is a BACKLOG: readings captured minutes or
                    # hours earlier and only now delivered. Their "status=4,
                    # speed=82" describes the past, not the present — feeding
                    # them to consumers made a scooter parked since 20:27
                    # look like it was riding all night (7 phantom trips on
                    # 2026-07-26). Publishing the readings live is therefore
                    # wrong; the ODO is what matters, and it is cumulative.
                    #
                    # So: mine the backlog for the highest odometer value
                    # (no kilometre lost) and publish a single reading that
                    # carries it with the CURRENT motion state — which, for
                    # a backlog, is by definition "not moving right now".
                    subs = [payload[i * Z_SUB_SIZE:(i + 1) * Z_SUB_SIZE]
                            for i in range(sub_count)]
                    log.info(
                        "Bundled Z frame: %d backlogged readings, replaying "
                        "odometer only (live motion state not inferred)",
                        sub_count,
                    )
                    data = (bytes([0x5A]) + (Z_SUB_SIZE + 6).to_bytes(2, 'big')
                            + bytes([1]) + bytes(subs[-1]) + bytes(2))
                    self._bundle_backlog = True

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


                # Backlogged bundle: the motion state it carries is historical.
                # It is only trustworthy while the odometer keeps advancing —
                # that is what tells a genuine ride (the module is behind but
                # the scooter IS rolling) apart from a parked scooter whose
                # module keeps re-sending its pending backlog for hours
                # (7 phantom trips on the night of 2026-07-26, odometer frozen
                # at 16681 from 20:54 to 01:18).
                if self._bundle_backlog:
                    self._bundle_backlog = False
                    odo_now = self.parameters.get("odo", {}).get("value")
                    try:
                        odo_now = float(odo_now)
                    except (TypeError, ValueError):
                        odo_now = None

                    advancing = (
                        odo_now is not None
                        and self._last_bundle_odo is not None
                        and odo_now > self._last_bundle_odo
                    )
                    first_bundle = self._last_bundle_odo is None
                    if odo_now is not None and 0 < odo_now < 1_000_000:
                        self._last_bundle_odo = odo_now

                    # A frozen odometer between two bundles is normal while
                    # riding (1 km resolution, red lights, traffic): only a
                    # freeze longer than FROZEN_ODO_GRACE_SECONDS proves the
                    # readings are a stale replay of a parked scooter.
                    now = _monotonic()
                    if advancing or first_bundle:
                        self._odo_frozen_since = None
                    elif self._odo_frozen_since is None:
                        self._odo_frozen_since = now

                    frozen_for = (
                        now - self._odo_frozen_since
                        if self._odo_frozen_since is not None else 0.0
                    )
                    if frozen_for > FROZEN_ODO_GRACE_SECONDS:
                        log.info(
                            "Backlogged bundle with an odometer frozen for %.0f s (%s): "
                            "reporting the scooter as stopped", frozen_for, odo_now,
                        )
                        for key, neutral in (("status", 0), ("speed", 0)):
                            if key in self.parameters:
                                self.parameters[key]["value"] = neutral
                        self.scooter_off = True

                # Validate parsed data before publishing — the last Z frame before
                # shutdown often contains corrupted values (odo=867M, energy=-55923, etc.)
                odo_val = self.parameters.get("odo", {}).get("value")
                if odo_val is not None and odo_val != "None":
                    try:
                        if float(odo_val) > 1000000 or float(odo_val) < 0:
                            log.warning("Corrupt Z frame detected (odo=%s), skipping publish", odo_val)
                            # Roll the cache back so the corrupt value cannot
                            # leak through a later publish.
                            self.parameters["odo"]["value"] = odo_prev
                            return
                    except (ValueError, TypeError):
                        pass

                # When the scooter is off, the extended CAN fields (populated
                # only via $RCAN polling during movement) keep their stale
                # last-known values indefinitely. Reset them to "None" so
                # consumers see a clean state that matches reality (no RPM,
                # no drive mode, no bus voltage when the scooter is off).
                if self.scooter_off:
                    for key in self._extended_can_keys:
                        if key in self.parameters:
                            self.parameters[key]["value"] = "None"

                log.debug(f"Message protocol Z parsed: {self.parameters}")
                pub.sendMessage(TOPIC_SCOOTER_STATUS, scooter_status = self.parameters)

            except Exception:
                log.exception(f"Exception in handling message protocol Z {data}")

    def parse_message_from_scooter_protocol_astra(self, data):

        if len(data) > 0:
            log.debug(f"Parse received message protocol astra from scooter: {data}")
            try:
                data = data.decode()

                # $STMS snapshot frame (forced sync via the SYNC command).
                # Full status in one CSV line; decoded separately because the
                # field layout differs entirely from $RCAN.
                if data.startswith("$STMS,"):
                    self._parse_stms(data)
                    return

                updated_keys = set(self._parse_extended_can(data))

                for parameter in self.RCAN_message_configuration:
                    if data[:len(self.RCAN_message_configuration[parameter]["header"])] == self.RCAN_message_configuration[parameter]["header"]:
                        byte_pos = self.RCAN_message_configuration[parameter]["message_byte_pos"]
                        positions = data.split(",")
                        combined_HEX = positions[byte_pos[1]] + positions[byte_pos[0]]
                        value = int(combined_HEX, 16)
                        # Cell voltages are 16-bit raw values; anything outside
                        # means a truncated/misaligned frame — never cache it.
                        if 0 <= value <= 65535:
                            self.parameters[parameter]["value"] = value
                            updated_keys.add(parameter)

                if not updated_keys:
                    # $RCAN,ER (bus CAN en erreur), heartbeat $ASTRA, trame
                    # inconnue : RIEN n'a été décodé. Ne PAS republier le
                    # cache : c'était la source des valeurs "fantômes" (un
                    # scooter garé mais éveillé qui spamme ER faisait
                    # republier vitesse/odo périmés + rafraîchir last-update
                    # pendant des dizaines de minutes).
                    log.debug("No parameter decoded from astra frame, cache not republished")
                    return

                updated = {k: self.parameters[k] for k in updated_keys if k in self.parameters}
                log.debug(f"Message protocol astra parsed, publishing {sorted(updated_keys)}")
                pub.sendMessage(TOPIC_SCOOTER_STATUS, scooter_status = updated)

            except Exception:
                log.exception(f"Exception in handling message protocol astra {data}")

    def _parse_stms(self, data):
        """Decode a $STMS snapshot frame and publish scooter status.

        Field indices reverse-engineered then cross-checked against live
        Z-protocol telemetry from the same scooter:
          SOC=82, Vbat=55.6 (556/10), Tmax=25, Tmin=24, range=109,
          charged=331.5425 (1193553/3600), regen=28.2425 (101673/3600),
          discharged=321.8067 (1158504/3600), VIN=UCYSxxxxxxxxxxxxx (17 chars).

        Example frame:
          $STMS,0,82,25,24,556,0,435036987,0,0,330,330,0,109,0,
                1193553,101673,1158504,22,<ICCID>,<phone>,<VIN>

        Indices 7 (large counter), 8-12 and 14 are unidentified and left
        untouched so they don't clobber good Z data — notably odo, which
        $STMS does not carry (Z odo=6345 km != field 7).
        """
        parts = data.strip().split(",")

        # (csv_index, parameter_key, divider)
        #
        # Index 1 is NOT mapped to "status" on purpose: its semantics are only
        # documented by a single parked-scooter capture (value 0). A SYNC
        # requested from the official app while riding would otherwise publish
        # status=0 and stop the trip in Home Assistant. Motion state stays
        # owned by the Z protocol frames.
        numeric_fields = [
            (2, "batterySOC", 1),
            (3, "batteryTempMax", 1),
            (4, "batteryTempMin", 1),
            (5, "batteryVoltage", 10),
            (6, "batteryCurrent", 10),
            (13, "range", 1),
            (15, "chargedEnergy", 3600),
            (16, "RegeneratedEnergy", 3600),
            (17, "DischargedEnergy", 3600),
            (18, "ambientTemp", 1),
        ]

        # Same contract as the $RCAN path: only the keys actually decoded
        # from THIS frame are published. Republishing the whole cache was the
        # root cause of the 2026-07 "ghost telemetry" (stale speed/odo
        # re-emitted on every frame).
        updated_keys = set()
        for index, key, divider in numeric_fields:
            if index >= len(parts):
                continue
            raw = parts[index].strip()
            if raw == "":
                continue
            try:
                self.parameters[key]["value"] = int(raw) / divider
                updated_keys.add(key)
            except (ValueError, KeyError):
                log.debug("STMS: cannot parse %s (index %s) value %r", key, index, raw)

        # VIN is the last CSV field; sanity-check it looks like a Silence VIN
        # before trusting it (guards against a truncated/garbled frame).
        if len(parts) >= 2:
            vin = parts[-1].strip()
            if vin.startswith("UCYS") and len(vin) >= 10:
                try:
                    self.parameters["VIN"]["value"] = vin
                    updated_keys.add("VIN")
                except KeyError:
                    pass

        if not updated_keys:
            log.warning("STMS frame had no decodable fields: %s", data)
            return

        updated = {k: self.parameters[k] for k in updated_keys if k in self.parameters}
        log.debug(f"Message $STMS parsed, publishing {sorted(updated_keys)}")
        pub.sendMessage(TOPIC_SCOOTER_STATUS, scooter_status=updated)

    def _parse_extended_can(self, data):
        """Parse extended CAN data from $RCAN responses.

        Returns the list of parameter keys actually updated so the caller
        can publish only fresh values (never the whole stale cache).
        """
        updated = []
        if not data.startswith("$RCAN,"):
            return updated

        parts = data.strip().split(",")
        if len(parts) < 3:
            return updated

        rcan_id = parts[1]

        try:
            # 0x280 - ECU: Drive mode + switches
            if rcan_id == "280" and len(parts) >= 5:
                byte0 = int(parts[3], 16)
                byte1 = int(parts[4], 16)
                self.parameters["driveReady"]["value"] = int(byte0 & 0x01 != 0)
                self.parameters["sidestandDown"]["value"] = int(byte0 & 0x08 != 0)
                mode_bits = (byte0 >> 4) & 0x03
                mode_map = {0: "OFF", 1: "ECO", 2: "SPORT", 3: "CITY"}
                self.parameters["driveMode"]["value"] = mode_map.get(mode_bits, "UNKNOWN")
                self.parameters["warningLights"]["value"] = int(byte1 & 0x01 != 0)
                updated += ["driveReady", "sidestandDown", "driveMode", "warningLights"]

            # 0x300 - Range by current drive mode
            elif rcan_id == "300" and len(parts) >= 5:
                self.parameters["rangeByMode"]["value"] = int(parts[4], 16)
                updated.append("rangeByMode")

            # 0x182 - BMS flags
            elif rcan_id == "182" and len(parts) >= 4:
                self.parameters["bmsFlags"]["value"] = int(parts[3], 16)
                updated.append("bmsFlags")

            # parts layout: $RCAN,{ID},{len},{b0},{b1},{b2},{b3},{b4},{b5},{b6},{b7},OK
            #                  0     1    2    3    4    5    6    7    8    9    10   11

            # 0x181 - BMS live current (bytes 6-7, signed LE, /10 = amps)
            elif rcan_id == "181" and len(parts) >= 11:
                b6 = int(parts[9], 16)
                b7 = int(parts[10], 16)
                current_raw = b6 | (b7 << 8)
                if current_raw > 32767:
                    current_raw -= 65536
                self.parameters["bmsCurrent"]["value"] = round(current_raw / 10.0, 1)
                updated.append("bmsCurrent")

            # 0x189 - Battery NTC temperatures (3 probes, bytes 2-7, /100 = celsius)
            elif rcan_id == "189" and len(parts) >= 11:
                ntc1 = int(parts[5], 16) | (int(parts[6], 16) << 8)
                ntc2 = int(parts[7], 16) | (int(parts[8], 16) << 8)
                ntc3 = int(parts[9], 16) | (int(parts[10], 16) << 8)
                self.parameters["batteryNTC1"]["value"] = round(ntc1 / 100.0, 1)
                self.parameters["batteryNTC2"]["value"] = round(ntc2 / 100.0, 1)
                self.parameters["batteryNTC3"]["value"] = round(ntc3 / 100.0, 1)
                updated += ["batteryNTC1", "batteryNTC2", "batteryNTC3"]

            # 0x391 - Motor RPM (bytes 4-5, unsigned LE)
            elif rcan_id == "391" and len(parts) >= 9:
                b4 = int(parts[7], 16)
                b5 = int(parts[8], 16)
                self.parameters["motorRPM"]["value"] = b4 | (b5 << 8)
                updated.append("motorRPM")

            # 0x381 - Motor power/torque (bytes 2-3, signed LE)
            elif rcan_id == "381" and len(parts) >= 7:
                b2 = int(parts[5], 16)
                b3 = int(parts[6], 16)
                power_raw = b2 | (b3 << 8)
                if power_raw > 32767:
                    power_raw -= 65536
                self.parameters["motorPower"]["value"] = power_raw
                updated.append("motorPower")

            # 0x371 - Votol bus voltage (bytes 6-7, unsigned LE, /10 = volts)
            elif rcan_id == "371" and len(parts) >= 11:
                b6 = int(parts[9], 16)
                b7 = int(parts[10], 16)
                self.parameters["busVoltage"]["value"] = round((b6 | (b7 << 8)) / 10.0, 1)
                updated.append("busVoltage")

        except (ValueError, IndexError, KeyError) as e:
            log.debug("Error parsing extended CAN %s: %s", rcan_id, e)

        return updated

    def get_scooter_off_status(self):
        return self.scooter_off
