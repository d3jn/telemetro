"""F1 25 UDP packet parsers.

Per-car format strings and sizes are hand-derived from the F1 25 telemetry
spec. Tuple indices below are positional against those formats — adding or
removing fields shifts every subsequent index, so reverify against the spec
when bumping game year.

Coordinate note: F1's Y axis is vertical (height). The horizontal plane is
X/Z, which is what the recorder writes as `world_x` / `world_z`.
"""

import struct


HEADER_FMT = "<HBBBBBQfIIBB"
HEADER_SIZE = struct.calcsize(HEADER_FMT)  # 29

# Motion: 6 floats (worldPos XYZ + worldVel XYZ), 6 int16 (forwardDir + rightDir),
# 6 floats (gForceLat/Long/Vert + yaw/pitch/roll). 60 bytes per car, 22 cars.
MOTION_DATA_FMT = "<6f6h6f"
MOTION_DATA_SIZE = struct.calcsize(MOTION_DATA_FMT)  # 60

# Car Telemetry: speed(H), throttle(f), steer(f), brake(f), clutch(B), gear(b),
# rpm(H), drs(B), revLightsPct(B), revLightsBits(H), brakeTemps[4](H),
# tyreSurfaceTemps[4](B), tyreInnerTemps[4](B), engineTemp(H), tyrePressures[4](f),
# surfaceTypes[4](B). 60 bytes per car.
CAR_TELEMETRY_FMT = "<HfffBbHBBH4H4B4BH4f4B"
CAR_TELEMETRY_SIZE = struct.calcsize(CAR_TELEMETRY_FMT)  # 60

# Lap data: same shape as f1-racing-companion's parser. 57 bytes per car.
LAP_DATA_FMT = "<IIHBHBHBHBfffBBBBBBBBBBBBBBBHHBfB"
LAP_DATA_SIZE = struct.calcsize(LAP_DATA_FMT)  # 57

# Car status: 55 bytes per car. ERS store energy at idx 19 (float J),
# deploy mode at idx 20 (uint8).
CAR_STATUS_FMT = "<BBBBBfffHHBBHBBBbfffBfffB"
CAR_STATUS_SIZE = struct.calcsize(CAR_STATUS_FMT)  # 55

# Participants: 57 bytes per car. `<7B32s2BH2B12B` =
# 7 uint8 (aiCtrl..nationality) + 32s name + 2 uint8 (yourTelem, showOnlineNames)
# + uint16 techLevel + 2 uint8 (platform, numColours) + 12 uint8 livery colours.
PARTICIPANT_DATA_FMT = "<7B32s2BH2B12B"
PARTICIPANT_DATA_SIZE = struct.calcsize(PARTICIPANT_DATA_FMT)  # 57

PACKET_MOTION = 0
PACKET_SESSION = 1
PACKET_LAP = 2
PACKET_PARTICIPANTS = 4
PACKET_CAR_TELEMETRY = 6
PACKET_CAR_STATUS = 7
PACKET_CAR_DAMAGE = 10

# Car Damage: 4 floats m_tyresWear[RL,RR,FL,FR] + 30 uint8 damage values.
# Matches f1-racing-companion's layout — verified against F1 25 spec there.
CAR_DAMAGE_FMT = "<4f30B"
CAR_DAMAGE_SIZE = struct.calcsize(CAR_DAMAGE_FMT)  # 46

# Session packet — only the pre-marshal block, where sessionType (idx 5) and
# trackId (idx 6) live. Layout matches f1-racing-companion's PRE_MARSHAL_FMT.
SESSION_PRE_FMT = "<BbbBHBbBHHBBBBBB"

# F1 25 track IDs → conventional short names used in filenames. Unknown IDs
# fall back to "track_<id>" rather than failing.
TRACK_NAMES = {
    0: "melbourne",
    1: "paul_ricard",
    2: "shanghai",
    3: "sakhir",
    4: "catalunya",
    5: "monaco",
    6: "montreal",
    7: "silverstone",
    8: "hockenheim",
    9: "hungaroring",
    10: "spa",
    11: "monza",
    12: "singapore",
    13: "suzuka",
    14: "abu_dhabi",
    15: "cota",
    16: "interlagos",
    17: "red_bull_ring",
    18: "sochi",
    19: "mexico",
    20: "baku",
    21: "sakhir_short",
    22: "silverstone_short",
    23: "cota_short",
    24: "suzuka_short",
    25: "hanoi",
    26: "zandvoort",
    27: "imola",
    28: "portimao",
    29: "jeddah",
    30: "miami",
    31: "las_vegas",
    32: "losail",
    33: "madrid",
}


def track_name(track_id):
    return TRACK_NAMES.get(track_id, f"track_{track_id}")


# F1 25 session types → short codes. Aligned with the F1 24/25 spec; sprint
# variants (14..19) use best-guess codes. Unknown IDs fall back to "S<n>".
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
    10: "R",
    11: "R2",
    12: "R3",
    13: "TT",
    14: "SS1",
    15: "SS2",
    16: "SS3",
    17: "SSP",
    18: "OSSQ",
    19: "Sprint",
}


def session_type_code(session_type):
    return SESSION_TYPE_CODES.get(session_type, f"S{session_type}")

# F1 25 ERS deploy modes — raw uint8 codes emitted to CSV. Mapping for
# downstream consumers: 0=none, 1=normal (game UI calls this "Medium"),
# 2=hotlap, 3=overtake.
ERS_MODES = {0: "none", 1: "normal", 2: "hotlap", 3: "overtake"}

# Max ERS store energy per the spec (4 MJ).
ERS_MAX_STORE_JOULES = 4_000_000.0

NUM_CARS = 22


def parse_header(data):
    h = struct.unpack_from(HEADER_FMT, data, 0)
    return {
        "packet_id": h[5],
        "session_uid": h[6],
        "session_time": h[7],
        "frame_id": h[8],
        "player_car_index": h[10],
    }


def parse_motion(data):
    out = []
    offset = HEADER_SIZE
    for _ in range(NUM_CARS):
        m = struct.unpack_from(MOTION_DATA_FMT, data, offset)
        out.append({"world_x": m[0], "world_z": m[2]})
        offset += MOTION_DATA_SIZE
    return out


def parse_car_telemetry(data):
    out = []
    offset = HEADER_SIZE
    for _ in range(NUM_CARS):
        t = struct.unpack_from(CAR_TELEMETRY_FMT, data, offset)
        # tyre temp arrays are RL, RR, FL, FR per F1 spec.
        out.append({
            "speed": t[0],
            "throttle": t[1] * 100.0,
            "steer": t[2] * 100.0,
            "brake": t[3] * 100.0,
            "gear": t[5],
            "tire_surface_temp": (t[14], t[15], t[16], t[17]),
            "tire_inner_temp": (t[18], t[19], t[20], t[21]),
        })
        offset += CAR_TELEMETRY_SIZE
    return out


def parse_lap(data):
    out = []
    offset = HEADER_SIZE
    for _ in range(NUM_CARS):
        l = struct.unpack_from(LAP_DATA_FMT, data, offset)
        out.append({
            # m_lastLapTimeInMS — game-authoritative final time of the
            # previous lap, set the moment S/F is crossed.
            "last_lap_time_ms": l[0],
            "lap_time_ms": l[1],
            # Sector times come split as (msPart:H, minutesPart:B) to support
            # >65s sectors. Combine to a single ms value. Each is 0 until the
            # car crosses that sector's boundary, then latched.
            "sector1_time_ms": l[3] * 60000 + l[2],
            "sector2_time_ms": l[5] * 60000 + l[4],
            "lap_distance": l[10],
            "lap_num": l[14],
            "pit_status": l[15],
            # 0=S1, 1=S2, 2=S3. Used downstream to identify the lap_distance
            # at which the car crossed each sector boundary.
            "sector_idx": l[17],
        })
        offset += LAP_DATA_SIZE
    return out


def parse_car_status(data):
    out = []
    offset = HEADER_SIZE
    for _ in range(NUM_CARS):
        s = struct.unpack_from(CAR_STATUS_FMT, data, offset)
        out.append({
            "fuel_in_tank": s[5],
            "ers_pct": (s[19] / ERS_MAX_STORE_JOULES) * 100.0,
            # Raw uint8 0..3 (see ERS_MODES for mapping).
            "ers_mode": s[20],
        })
        offset += CAR_STATUS_SIZE
    return out


def parse_car_damage(data):
    out = []
    offset = HEADER_SIZE
    for _ in range(NUM_CARS):
        d = struct.unpack_from(CAR_DAMAGE_FMT, data, offset)
        # m_tyresWear[RL, RR, FL, FR] as percentages.
        out.append({"tire_wear": (d[0], d[1], d[2], d[3])})
        offset += CAR_DAMAGE_SIZE
    return out


def parse_session(data):
    s = struct.unpack_from(SESSION_PRE_FMT, data, HEADER_SIZE)
    return {
        "session_type": s[5],
        "session_type_code": session_type_code(s[5]),
        "track_id": s[6],
        "track_name": track_name(s[6]),
    }


def parse_participants(data):
    out = []
    offset = HEADER_SIZE + 1  # skip m_numActiveCars
    for _ in range(NUM_CARS):
        p = struct.unpack_from(PARTICIPANT_DATA_FMT, data, offset)
        raw_name = p[7]
        name = raw_name.split(b"\x00", 1)[0].decode("utf-8", errors="replace").strip()
        out.append({
            "name": name,
            "race_number": p[5],
            # 0 = restricted (game zeroes Motion + Car Telemetry for this driver),
            # 1 = public.
            "your_telemetry": p[8],
        })
        offset += PARTICIPANT_DATA_SIZE
    return out
