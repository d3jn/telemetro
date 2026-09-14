"""Tests for the telemetry parsers and the CSV recorder.

The 2026 path cannot be exercised without the Season Pack and a live session, so
these tests build synthetic packets instead. The struct layouts below are written
out field by field from the published specs rather than reused from the parsers,
so an offset or width typo in a parser cannot quietly agree with itself here.

Run from the project root:

    python3 -m unittest discover -t . -s tests -v
"""

import csv
import os
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import telemetry
from recorder import ROW_FIELDS, Recorder
from telemetry import base


# --- layouts restated from the specs -----------------------------------------

# PacketHeader: packetFormat, gameYear, gameMajor, gameMinor, packetVersion,
# packetId, sessionUID, sessionTime, frameIdentifier, overallFrameIdentifier,
# playerCarIndex, secondaryPlayerCarIndex.
HEADER = "<HBBBBBQfIIBB"

# CarMotionData: 3f position, 3f velocity, 3h forward, 3h right, then g-force
# (3f in 2025, 3h quantised in 2026), then yaw, pitch, roll.
MOTION = {"2025": "<3f3f3h3h3ffff", "2026": "<3f3f3h3h3hfff"}

# LapData, identical in both formats: lastLapTime, currentLapTime, four
# (ms, minutes) time pairs, lapDistance / totalDistance / safetyCarDelta,
# carPosition, currentLapNum, pitStatus, numPitStops, sector, then ten more uint8
# status fields, two pit timers, shouldServePen, speed trap speed and lap.
LAP = "<2I" + "HB" * 4 + "3f" + "5B" + "10B" + "2H" + "B" + "f" + "B"

# ParticipantData: aiControlled, driverId, networkId, teamId (the middle three
# widen to uint16 in 2026), myTeam, raceNumber, nationality, name[32],
# yourTelemetry, showOnlineNames, techLevel, platform, numColours, 4x RGB livery.
PARTICIPANT = {"2025": "<BBBBBBB32sBBHBB12B", "2026": "<BHHHBBB32sBBHBB12B"}

# CarTelemetryData: speed, throttle, steer, brake, clutch, gear, engineRPM, drs,
# revLightsPercent, revLightsBitValue, brakesTemperature[4],
# tyresSurfaceTemperature[4], tyresInnerTemperature[4], engineTemperature
# (uint16 in 2025, uint8 in 2026), tyresPressure[4], surfaceType[4].
CAR_TELEMETRY = {
    "2025": "<HfffBbHBBH" + "4H" + "4B" + "4B" + "H" + "4f" + "4B",
    "2026": "<HfffBbHBBH" + "4H" + "4B" + "4B" + "B" + "4f" + "4B",
}
# Trailing mfdPanelIndex, mfdPanelIndexSecondaryPlayer, suggestedGear.
CAR_TELEMETRY_TRAILER = "<BBb"

# CarStatusData: tractionControl, antiLockBrakes, fuelMix, frontBrakeBias,
# pitLimiterStatus, fuelInTank, fuelCapacity, fuelRemainingLaps, maxRPM, idleRPM,
# maxGears, drsAllowed, drsActivationDistance, actualTyreCompound,
# visualTyreCompound, tyresAgeLaps, vehicleFIAFlags, enginePowerICE,
# enginePowerMGUK, ersStoreEnergy, ersDeployMode, ersHarvestedThisLapMGUK,
# ersHarvestedThisLapMGUH, [2026: ersHarvestLimitPerLap], ersDeployedThisLap,
# networkPaused.
CAR_STATUS = {
    "2025": "<5B" + "3f" + "2H" + "2B" + "H" + "3B" + "b" + "3f" + "B" + "2f" + "f" + "B",
    "2026": "<5B" + "3f" + "2H" + "2B" + "H" + "3B" + "b" + "3f" + "B" + "2f" + "f" + "f" + "B",
}

# CarDamageData, identical in both formats: tyresWear[4] (f), tyresDamage[4],
# brakesDamage[4], tyreBlisters[4], six bodywork values, drsFault, ersFault,
# gearBoxDamage, engineDamage, six engine wear values, engineBlown, engineSeized.
CAR_DAMAGE = "<4f" + "4B4B4B" + "6B" + "2B" + "2B" + "6B" + "2B"

# CarTelemetry2Data (2026 only): activeAeroMode, activeAeroAvailable,
# activeAeroActivationDistance, overtakeAvailable, overtakeActive,
# overtakeActivationDistance, 2026Regulations, drivingWrongWay.
CAR_TELEMETRY2 = "<BBHBBHBB"

# PacketSessionData up to m_trackId: weather, trackTemperature, airTemperature,
# totalLaps, trackLength, sessionType, trackId.
SESSION_PREFIX = "<BbbBHBb"

# Packet sizes the specs state, as an independent check on our arithmetic.
SPEC_SIZES = {
    "2025": {"motion": 1349, "lap": 1285, "participants": 1284,
             "car_telemetry": 1352, "car_status": 1239, "car_damage": 1041},
    "2026": {"motion": 1325, "lap": 1399, "participants": 1470,
             "car_telemetry": 1448, "car_status": 1445, "car_damage": 1133,
             "car_telemetry2": 269},
}

F2025 = telemetry.FORMATS["2025"]
F2026 = telemetry.FORMATS["2026"]
FORMATS = [F2025, F2026]


# --- packet builders ----------------------------------------------------------

def header(fmt, packet_id, session_uid=0xABCD, frame=99, player_idx=0):
    return struct.pack(HEADER, fmt.packet_format, int(fmt.name) % 100, 1, 5, 1,
                       packet_id, session_uid, 12.5, frame, frame, player_idx, 255)


def per_car(fmt, entries, default):
    return b"".join(entries.get(i, default) for i in range(fmt.num_cars))


def motion_car(fmt, pos=(0.0, 0.0, 0.0)):
    return struct.pack(MOTION[fmt.name], *pos, 0.0, 0.0, 0.0, 0, 0, 32767, 32767, 0, 0,
                       1, 2, 3, 0.0, 0.0, 0.0)


def motion_packet(fmt, cars=None, **kw):
    return header(fmt, 0, **kw) + per_car(fmt, cars or {}, motion_car(fmt))


def lap_entry(last_lap=0, cur_lap_time=0, s1=(0, 0), s2=(0, 0), lap_distance=0.0,
              lap_num=1, pit_status=0, sector=0):
    return struct.pack(LAP, last_lap, cur_lap_time, s1[0], s1[1], s2[0], s2[1], 0, 0, 0, 0,
                       lap_distance, 1000.0, 0.0,
                       1, lap_num, pit_status, 0, sector,
                       0, 0, 0, 0, 0, 0, 0, 0, 4, 2,
                       0, 0, 0, 0.0, 255)


def lap_packet(fmt, entries=None, **kw):
    return header(fmt, 2, **kw) + per_car(fmt, entries or {}, lap_entry()) + b"\xff\xff"


def participant(fmt, name=b"", race_number=0, your_telemetry=1, team_id=0):
    return struct.pack(PARTICIPANT[fmt.name], 1, 0, 0, team_id, 0, race_number, 0, name,
                       your_telemetry, 1, 0, 1, 0, *([0] * 12))


def participants_packet(fmt, entries=None, **kw):
    entries = entries or {}
    return header(fmt, 4, **kw) + bytes([len(entries)]) + per_car(fmt, entries, participant(fmt))


def telemetry_car(fmt, speed=0, throttle=0.0, steer=0.0, brake=0.0, gear=0, drs=0,
                  surface=(0, 0, 0, 0), inner=(0, 0, 0, 0)):
    return struct.pack(CAR_TELEMETRY[fmt.name], speed, throttle, steer, brake, 0, gear,
                       11000, drs, 50, 0, 500, 500, 500, 500, *surface, *inner, 110,
                       23.0, 23.0, 22.0, 22.0, 0, 0, 0, 0)


def telemetry_packet(fmt, cars=None, **kw):
    body = per_car(fmt, cars or {}, telemetry_car(fmt))
    return header(fmt, 6, **kw) + body + struct.pack(CAR_TELEMETRY_TRAILER, 255, 255, 0)


def status_car(fmt, fuel=0.0, ers_store=0.0, ers_mode=0):
    harvest = (1.0, 2.0, 3.0) if fmt.name == "2026" else (1.0, 2.0)
    return struct.pack(CAR_STATUS[fmt.name], 0, 0, 1, 55, 0, fuel, 100.0, 20.0, 13000, 3500,
                       8, 0, 0, 18, 17, 3, 0, 500000.0, 120000.0, ers_store, ers_mode,
                       *harvest, 4.0, 0)


def status_packet(fmt, cars=None, **kw):
    return header(fmt, 7, **kw) + per_car(fmt, cars or {}, status_car(fmt))


def damage_car(wear=(0.0, 0.0, 0.0, 0.0)):
    return struct.pack(CAR_DAMAGE, *wear, *([7] * 30))


def damage_packet(fmt, cars=None, **kw):
    return header(fmt, 10, **kw) + per_car(fmt, cars or {}, damage_car())


def telemetry2_car(aero_mode=0, overtake_active=0, regs_2026=1):
    return struct.pack(CAR_TELEMETRY2, aero_mode, 1, 0, 1, overtake_active, 0, regs_2026, 0)


def telemetry2_packet(fmt, cars=None, **kw):
    return header(fmt, 16, **kw) + per_car(fmt, cars or {}, telemetry2_car())


def session_packet(fmt, track_id=10, session_type=15, **kw):
    return header(fmt, 1, **kw) + struct.pack(
        SESSION_PREFIX, 0, 30, 25, 44, 7004, session_type, track_id) + bytes(700)


BUILDERS = {
    "motion": motion_packet,
    "lap": lap_packet,
    "participants": participants_packet,
    "car_telemetry": telemetry_packet,
    "car_status": status_packet,
    "car_damage": damage_packet,
    "car_telemetry2": telemetry2_packet,
}


def payload(fmt, packet):
    parsed = fmt.parse(packet)
    assert parsed is not None, "packet rejected"
    return parsed[2]


# --- parser tests -------------------------------------------------------------

class LayoutTest(unittest.TestCase):
    """Sizes built from the spec layouts must match the sizes the specs state."""

    def test_header_size(self):
        self.assertEqual(base.HEADER_SIZE, 29)

    def test_packet_sizes_match_spec(self):
        for fmt in FORMATS:
            for kind, size in SPEC_SIZES[fmt.name].items():
                with self.subTest(fmt=fmt.name, kind=kind):
                    self.assertEqual(len(BUILDERS[kind](fmt)), size)

    def test_parser_layouts_match_independent_layouts(self):
        for fmt in FORMATS:
            with self.subTest(fmt=fmt.name):
                self.assertEqual(fmt.car_motion.size, struct.calcsize(MOTION[fmt.name]))
                self.assertEqual(fmt.lap_data.size, struct.calcsize(LAP))
                self.assertEqual(fmt.participant.size, struct.calcsize(PARTICIPANT[fmt.name]))
                self.assertEqual(fmt.car_telemetry.size, struct.calcsize(CAR_TELEMETRY[fmt.name]))
                self.assertEqual(fmt.car_status.size, struct.calcsize(CAR_STATUS[fmt.name]))
                self.assertEqual(fmt.car_damage.size, struct.calcsize(CAR_DAMAGE))
        self.assertEqual(F2026.car_telemetry2.size, struct.calcsize(CAR_TELEMETRY2))
        self.assertIsNone(F2025.car_telemetry2)

    def test_car_counts(self):
        self.assertEqual(F2025.num_cars, 22)
        self.assertEqual(F2026.num_cars, 24)

    def test_regs(self):
        self.assertEqual(F2025.regs, telemetry.REGS_2025)
        self.assertIsNone(F2026.regs)


class ParseTest(unittest.TestCase):
    """Every field the recorder reads round-trips in both formats, including in
    the last car slot, which catches a wrong per-car size."""

    def test_motion(self):
        for fmt in FORMATS:
            last = fmt.num_cars - 1
            cars = payload(fmt, motion_packet(fmt, {last: motion_car(fmt, (1.5, -2.0, 3.25))}))
            self.assertEqual(len(cars), fmt.num_cars)
            self.assertEqual(cars[last], {"world_x": 1.5, "world_z": 3.25})

    def test_lap(self):
        for fmt in FORMATS:
            last = fmt.num_cars - 1
            entry = lap_entry(last_lap=91234, cur_lap_time=45000, s1=(12345, 1), s2=(500, 0),
                              lap_distance=2500.5, lap_num=7, pit_status=2, sector=1)
            lap = payload(fmt, lap_packet(fmt, {last: entry}))[last]
            self.assertEqual(lap, {
                "last_lap_time_ms": 91234,
                "lap_time_ms": 45000,
                "sector1_time_ms": 72345,
                "sector2_time_ms": 500,
                "lap_distance": 2500.5,
                "lap_num": 7,
                "pit_status": 2,
                "sector_idx": 1,
            })

    def test_participants(self):
        for fmt in FORMATS:
            last = fmt.num_cars - 1
            # 485 (Audi '26) only fits the 2026 uint16 team id.
            team_id = 485 if fmt.name == "2026" else 9
            entry = participant(fmt, b"Driver \xc3\x96ne", 27, your_telemetry=0, team_id=team_id)
            p = payload(fmt, participants_packet(fmt, {last: entry}))[last]
            self.assertEqual(p, {"name": "Driver Öne", "race_number": 27, "your_telemetry": 0})

    def test_car_telemetry(self):
        for fmt in FORMATS:
            last = fmt.num_cars - 1
            car = telemetry_car(fmt, speed=312, throttle=1.0, steer=-0.5, brake=0.25, gear=-1,
                                drs=1, surface=(90, 91, 92, 93), inner=(100, 101, 102, 103))
            t = payload(fmt, telemetry_packet(fmt, {last: car}))[last]
            self.assertEqual(t, {
                "speed": 312, "throttle": 100.0, "steer": -50.0, "brake": 25.0, "gear": -1,
                "drs": 1,
                "tire_surface_temp": (90, 91, 92, 93),
                "tire_inner_temp": (100, 101, 102, 103),
            })

    def test_car_status(self):
        for fmt in FORMATS:
            last = fmt.num_cars - 1
            car = status_car(fmt, fuel=42.5, ers_store=3_000_000.0, ers_mode=3)
            s = payload(fmt, status_packet(fmt, {last: car}))[last]
            self.assertEqual(s, {"fuel_in_tank": 42.5, "ers_store": 3_000_000.0, "ers_mode": 3})

    def test_car_damage(self):
        for fmt in FORMATS:
            last = fmt.num_cars - 1
            d = payload(fmt, damage_packet(fmt, {last: damage_car((1.5, 2.5, 3.5, 4.5))}))[last]
            self.assertEqual(d, {"tire_wear": (1.5, 2.5, 3.5, 4.5)})

    def test_car_telemetry2(self):
        last = F2026.num_cars - 1
        cars = payload(F2026, telemetry2_packet(F2026, {
            0: telemetry2_car(aero_mode=1, overtake_active=0, regs_2026=0),
            last: telemetry2_car(aero_mode=1, overtake_active=1, regs_2026=1),
        }))
        self.assertEqual(cars[0], {"regs": 2025, "active_aero_mode": 1, "overtake_active": 0})
        self.assertEqual(cars[last], {"regs": 2026, "active_aero_mode": 1, "overtake_active": 1})

    def test_session(self):
        for fmt in FORMATS:
            self.assertEqual(payload(fmt, session_packet(fmt, track_id=20, session_type=15)), {
                "session_type": 15, "session_type_code": "R",
                "track_id": 20, "track_name": "baku",
            })

    def test_session_codes_follow_spec_appendix(self):
        for fmt in FORMATS:
            self.assertEqual(fmt.session_type_code(10), "SS1")
            self.assertEqual(fmt.session_type_code(18), "TT")
            self.assertEqual(fmt.session_type_code(99), "S99")

    def test_madrid_is_2026_only(self):
        self.assertEqual(F2026.track_name(42), "madrid")
        self.assertEqual(F2025.track_name(42), "track_42")

    def test_rejects_short_packets(self):
        # Bytes after the per-car array, which a length check must not count.
        trailers = {"lap": 2, "car_telemetry": 3}
        for fmt in FORMATS:
            for kind in SPEC_SIZES[fmt.name]:
                with self.subTest(fmt=fmt.name, kind=kind):
                    packet = BUILDERS[kind](fmt)
                    self.assertIsNone(fmt.parse(packet[:-(trailers.get(kind, 0) + 1)]))


class FormatIsolationTest(unittest.TestCase):
    def test_cross_format_packets_rejected(self):
        for kind in SPEC_SIZES["2025"]:
            with self.subTest(kind=kind):
                self.assertIsNone(F2026.parse(BUILDERS[kind](F2025)))
                self.assertIsNone(F2025.parse(BUILDERS[kind](F2026)))

    def test_2026_participants_would_pass_a_2025_length_check(self):
        # Why parse() checks m_packetFormat rather than trusting lengths alone.
        packet = participants_packet(F2026)
        self.assertGreater(len(packet), SPEC_SIZES["2025"]["participants"])
        self.assertIsNone(F2025.parse(packet))

    def test_car_telemetry2_ignored_by_2025(self):
        packet = header(F2025, 16) + bytes(22 * 10)
        self.assertIsNone(F2025.parse(packet))

    def test_truncated_header(self):
        self.assertIsNone(F2025.parse(b"\xe9\x07"))
        self.assertIsNone(telemetry.detect(b"\xe9\x07"))


class RegistryTest(unittest.TestCase):
    def test_get(self):
        self.assertIs(telemetry.get("2025"), F2025)
        self.assertIs(telemetry.get(" 2026 "), F2026)
        self.assertIsNone(telemetry.get("AUTO"))
        with self.assertRaises(ValueError):
            telemetry.get("2024")

    def test_detect(self):
        self.assertIs(telemetry.detect(motion_packet(F2025)), F2025)
        self.assertIs(telemetry.detect(motion_packet(F2026)), F2026)
        self.assertIsNone(telemetry.detect(struct.pack("<H", 2024) + bytes(40)))

    def test_settings_udp_format_is_valid(self):
        import json
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "settings.json"), encoding="utf-8") as f:
            telemetry.get(json.load(f).get("udp_format", telemetry.AUTO))


# --- recorder tests -----------------------------------------------------------

class RecorderTest(unittest.TestCase):
    """Drives packets through fmt.parse() into the Recorder, the way main.py
    does, and checks the CSV comes out in the one shared layout."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.out = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def feed(self, recorder, fmt, packet):
        kind, hdr, data = fmt.parse(packet)
        handler = {
            telemetry.KIND_MOTION: recorder.on_motion,
            telemetry.KIND_LAP: recorder.on_lap,
            telemetry.KIND_CAR_STATUS: recorder.on_car_status,
            telemetry.KIND_CAR_DAMAGE: recorder.on_car_damage,
            telemetry.KIND_CAR_TELEMETRY: recorder.on_car_telemetry,
            telemetry.KIND_CAR_TELEMETRY2: recorder.on_car_telemetry2,
        }.get(kind)
        if kind == telemetry.KIND_PARTICIPANTS:
            recorder.on_participants(data)
        elif kind == telemetry.KIND_SESSION:
            recorder.on_session(data)
        else:
            handler(hdr, data)

    def rows(self):
        files = sorted(n for n in os.listdir(self.out) if ".legacy_" not in n)
        result = {}
        for name in files:
            with open(os.path.join(self.out, name), encoding="utf-8", newline="") as f:
                reader = csv.DictReader(f)
                self.assertEqual(reader.fieldnames, ROW_FIELDS)
                result[name] = list(reader)
        return result

    def record(self, fmt, drivers, telemetry2=None, frames=1):
        """Send one session's worth of packets. ``drivers`` maps car index to
        name; every driver gets the same car state apart from Car Telemetry 2."""
        recorder = Recorder(self.out, fmt.num_cars, fmt.regs)
        # Telemetry first: the first packet of a new session UID starts the
        # session, and rows wait for Session + Participants.
        self.feed(recorder, fmt, telemetry_packet(fmt, frame=1))
        self.feed(recorder, fmt, session_packet(fmt, track_id=42 if fmt.name == "2026" else 20,
                                                session_type=15, frame=1))
        self.feed(recorder, fmt, participants_packet(
            fmt, {i: participant(fmt, n.encode()) for i, n in drivers.items()}, frame=1))
        for frame in range(2, 2 + frames):
            car_idx = list(drivers)
            self.feed(recorder, fmt, motion_packet(
                fmt, {i: motion_car(fmt, (10.0, 0.0, 20.0)) for i in car_idx}, frame=frame))
            self.feed(recorder, fmt, lap_packet(
                fmt, {i: lap_entry(lap_distance=100.0 * frame, lap_num=2) for i in car_idx},
                frame=frame))
            self.feed(recorder, fmt, status_packet(
                fmt, {i: status_car(fmt, 50.0, 2_000_000.0, 3) for i in car_idx}, frame=frame))
            self.feed(recorder, fmt, damage_packet(fmt, frame=frame))
            if telemetry2 is not None:
                self.feed(recorder, fmt, telemetry2_packet(fmt, telemetry2, frame=frame))
            self.feed(recorder, fmt, telemetry_packet(
                fmt, {i: telemetry_car(fmt, speed=250, throttle=1.0, drs=1) for i in car_idx},
                frame=frame))
        recorder.close()
        return self.rows()

    def test_2025_format(self):
        files = self.record(F2025, {0: "Alpha", 21: "Omega"})
        self.assertEqual(len(files), 2)
        name = next(n for n in files if "Omega" in n)
        self.assertTrue(name.endswith("_baku_Omega_R.csv"), name)
        row = files[name][0]
        self.assertEqual(row["regs"], "2025")
        self.assertEqual(row["low_drag"], "1")         # from m_drs
        self.assertEqual(row["overtake_active"], "")   # no such thing in 2025
        self.assertEqual(row["ers_store"], "2000000")
        self.assertEqual(row["ers_mode"], "3")
        self.assertEqual(row["speed"], "250")
        self.assertEqual(row["world_z"], "20.000")

    def test_2026_format_mixes_regs_per_car(self):
        files = self.record(F2026, {0: "Legacy", 23: "Modern"}, telemetry2={
            # A pre-2026 car: its wing state is m_drs, not active aero.
            0: telemetry2_car(aero_mode=0, overtake_active=1, regs_2026=0),
            # The 24th slot only exists in 2026.
            23: telemetry2_car(aero_mode=0, overtake_active=1, regs_2026=1),
        })
        self.assertEqual(len(files), 2)
        legacy = next(rows for n, rows in files.items() if "Legacy" in n)[0]
        modern = next(rows for n, rows in files.items() if "Modern" in n)[0]
        self.assertEqual((legacy["regs"], legacy["low_drag"], legacy["overtake_active"]),
                         ("2025", "1", ""))
        self.assertEqual((modern["regs"], modern["low_drag"], modern["overtake_active"]),
                         ("2026", "0", "1"))
        self.assertTrue(any(n.endswith("_madrid_Modern_R.csv") for n in files))

    def test_2026_rows_wait_for_car_telemetry2(self):
        files = self.record(F2026, {0: "Driver"}, telemetry2=None, frames=3)
        self.assertEqual(sum(len(rows) for rows in files.values()), 0)

    def test_old_schema_file_is_rotated(self):
        from datetime import datetime
        name = f"{datetime.now():%Y_%m_%d}_baku_Alpha_R.csv"
        with open(os.path.join(self.out, name), "w", encoding="utf-8") as f:
            f.write("lap_num,ers_pct\n1,50\n")
        files = self.record(F2025, {0: "Alpha"})
        self.assertEqual(len(files[name]), 1)
        self.assertTrue(any(".legacy_" in n for n in os.listdir(self.out)))


if __name__ == "__main__":
    unittest.main()
