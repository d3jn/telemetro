"""F1 25 UDP 2025 format.

Reference: "F1 25 Telemetry Output Structures.txt" and "Data Output from F1 25
v3.pdf" (structures and appendices), as kept in f1-race-control/specs/25vs26.
"""

import struct

from .base import REGS_2025, TelemetryFormat

NUM_CARS = 22

# CarMotionData - 60 bytes: 3f position, 3f velocity, 3h forward, 3h right,
# 3f g-force, 3f yaw/pitch/roll. Packet: 29 + 22 * 60 = 1349 bytes.
CAR_MOTION = struct.Struct("<6f6h6f")

# LapData - 57 bytes. Packet: 29 + 22 * 57 + 2 = 1285 bytes.
# Byte-identical in the 2026 format; only the array count grew.
LAP_DATA = struct.Struct("<IIHBHBHBHBfffBBBBBBBBBBBBBBBHHBfB")

# ParticipantData - 57 bytes: aiControlled, driverId, networkId, teamId, myTeam,
# raceNumber, nationality (all uint8), name[32], yourTelemetry, showOnlineNames,
# techLevel (uint16), platform, numColours, 4 x RGB livery.
# Packet: 29 + 1 + 22 * 57 = 1284 bytes.
PARTICIPANT = struct.Struct("<7B32s2BH2B12B")

# CarTelemetryData - 60 bytes: speed(H), throttle, steer, brake (f), clutch(B),
# gear(b), rpm(H), drs(B), revLightsPct(B), revLightsBits(H), brakeTemps[4](H),
# tyreSurfaceTemps[4](B), tyreInnerTemps[4](B), engineTemp(H), tyrePressures[4](f),
# surfaceTypes[4](B). Packet: 29 + 22 * 60 + 3 = 1352 bytes.
CAR_TELEMETRY = struct.Struct("<HfffBbHBBH4H4B4BH4f4B")

# CarStatusData - 55 bytes. ersStoreEnergy at idx 19, ersDeployMode at idx 20.
# Packet: 29 + 22 * 55 = 1239 bytes.
CAR_STATUS = struct.Struct("<BBBBBfffHHBBHBBBbfffBfffB")

# CarDamageData - 46 bytes: tyresWear[4](f) + 30 uint8 damage values.
# Byte-identical in the 2026 format. Packet: 29 + 22 * 46 = 1041 bytes.
CAR_DAMAGE = struct.Struct("<4f30B")

# Appendix "Track IDs" -> filename slugs.
TRACK_NAMES = {
    0: "melbourne",
    2: "shanghai",
    3: "sakhir",
    4: "catalunya",
    5: "monaco",
    6: "montreal",
    7: "silverstone",
    9: "hungaroring",
    10: "spa",
    11: "monza",
    12: "singapore",
    13: "suzuka",
    14: "abu_dhabi",
    15: "cota",
    16: "interlagos",
    17: "red_bull_ring",
    19: "mexico",
    20: "baku",
    26: "zandvoort",
    27: "imola",
    29: "jeddah",
    30: "miami",
    31: "las_vegas",
    32: "losail",
    39: "silverstone_reverse",
    40: "red_bull_ring_reverse",
    41: "zandvoort_reverse",
}

# Appendix "Session types" -> short codes. Identical in the 2026 spec.
SESSION_TYPE_CODES = {
    0: "UNK",
    1: "P1",
    2: "P2",
    3: "P3",
    4: "SP",
    5: "Q1",
    6: "Q2",
    7: "Q3",
    8: "SQ",
    9: "OSQ",
    10: "SS1",
    11: "SS2",
    12: "SS3",
    13: "SSS",
    14: "OSSS",
    15: "R",
    16: "R2",
    17: "R3",
    18: "TT",
}


class F1_2025Format(TelemetryFormat):
    name = "2025"
    packet_format = 2025
    num_cars = NUM_CARS
    regs = REGS_2025

    track_names = TRACK_NAMES
    session_type_codes = SESSION_TYPE_CODES

    car_motion = CAR_MOTION
    lap_data = LAP_DATA
    participant = PARTICIPANT
    car_telemetry = CAR_TELEMETRY
    car_status = CAR_STATUS
    car_damage = CAR_DAMAGE


FORMAT = F1_2025Format()
