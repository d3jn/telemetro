"""The format-neutral half of the telemetry contract.

Everything in this package turns raw UDP bytes into the plain per-car dicts the
recorder consumes. Nothing above it knows a struct offset, a packet size, or a
numeric track / session id.

Adding a new wire format means writing one module here that subclasses
TelemetryFormat and registering it in __init__.py. No application code changes.

Coordinate note: F1's Y axis is vertical (height). The horizontal plane is X/Z,
which is what the recorder writes as `world_x` / `world_z`.
"""

import struct

# The packet header is byte-identical across the 2025 and 2026 formats, so it is
# parsed once here rather than per format.
HEADER = struct.Struct("<HBBBBBQfIIBB")
HEADER_SIZE = HEADER.size  # 29

# Neutral packet kinds. The dispatch loop keys on these, never on the game's
# numeric packet ids, which a future format is free to renumber.
KIND_MOTION = "motion"
KIND_SESSION = "session"
KIND_LAP = "lap"
KIND_PARTICIPANTS = "participants"
KIND_CAR_TELEMETRY = "car_telemetry"
KIND_CAR_STATUS = "car_status"
KIND_CAR_DAMAGE = "car_damage"
KIND_CAR_TELEMETRY2 = "car_telemetry2"

# Regulation sets a car can run under. Distinct from the wire format: the 2026
# format carries pre-2026 cars too, flagged per car in Car Telemetry 2.
REGS_2025 = 2025
REGS_2026 = 2026

# ERS deploy modes - raw uint8 codes written to CSV. The 2026 spec renamed mode 3
# from "overtake" to "boost" to match official naming; it is the same mode, and
# unrelated to the 2026 Overtake Mode reported in Car Telemetry 2.
ERS_MODES = {0: "none", 1: "medium", 2: "hotlap", 3: "boost"}

# Session packet, up to m_trackId: weather, trackTemperature, airTemperature,
# totalLaps, trackLength, sessionType, trackId. Unchanged in 2025 and 2026.
SESSION_PREFIX = struct.Struct("<BbbBHBb")


class TelemetryFormat:
    """One UDP wire format.

    Subclasses set the identity, table and layout attributes below. Every parse
    hook returns None when the packet is too short to trust, which is the only
    length checking in the program.

    Payload shapes, all plain dicts:

      KIND_MOTION          list of num_cars {world_x, world_z}
      KIND_SESSION         {session_type, session_type_code, track_id, track_name}
      KIND_LAP             list of num_cars {last_lap_time_ms, lap_time_ms,
                           sector1_time_ms, sector2_time_ms, lap_distance,
                           lap_num, pit_status, sector_idx}
      KIND_PARTICIPANTS    list of num_cars {name, race_number, your_telemetry}
      KIND_CAR_TELEMETRY   list of num_cars {speed, throttle, steer, brake, gear,
                           drs, tire_surface_temp, tire_inner_temp}
      KIND_CAR_STATUS      list of num_cars {fuel_in_tank, ers_store, ers_mode}
      KIND_CAR_DAMAGE      list of num_cars {tire_wear}
      KIND_CAR_TELEMETRY2  list of num_cars {regs, active_aero_mode,
                           overtake_active}

    Four-wheel tuples are ordered RL, RR, FL, FR, as in the spec.
    """

    # --- identity ---
    name = ""            # value accepted by settings.json "udp_format"
    packet_format = 0    # the header m_packetFormat value this parser claims
    num_cars = 0         # length of every per-car array in this format
    # Regulation set every car in this format runs under, or None when it varies
    # per car and has to be read from Car Telemetry 2.
    regs = None

    # --- appendix tables ---
    track_names = {}         # track id -> filename slug
    session_type_codes = {}  # session type id -> short code

    # --- per-car layouts (struct.Struct) ---
    car_motion = None
    lap_data = None
    participant = None
    car_telemetry = None
    car_status = None
    car_damage = None
    car_telemetry2 = None    # None: the packet does not exist in this format

    # Packet ids. Identical in 2025 and 2026, but owned by the format so a future
    # one can renumber them without the dispatch loop noticing.
    packet_id_motion = 0
    packet_id_session = 1
    packet_id_lap = 2
    packet_id_participants = 4
    packet_id_car_telemetry = 6
    packet_id_car_status = 7
    packet_id_car_damage = 10
    packet_id_car_telemetry2 = 16

    def __init__(self):
        # Built once per format rather than per datagram: parse() runs for every
        # packet the game sends, several hundred a second.
        self._handlers = {
            self.packet_id_motion: (KIND_MOTION, self._parse_motion),
            self.packet_id_session: (KIND_SESSION, self._parse_session),
            self.packet_id_lap: (KIND_LAP, self._parse_lap),
            self.packet_id_participants: (KIND_PARTICIPANTS, self._parse_participants),
            self.packet_id_car_telemetry: (KIND_CAR_TELEMETRY, self._parse_car_telemetry),
            self.packet_id_car_status: (KIND_CAR_STATUS, self._parse_car_status),
            self.packet_id_car_damage: (KIND_CAR_DAMAGE, self._parse_car_damage),
        }
        if self.car_telemetry2 is not None:
            self._handlers[self.packet_id_car_telemetry2] = (
                KIND_CAR_TELEMETRY2, self._parse_car_telemetry2)

    @staticmethod
    def parse_header(data):
        if len(data) < HEADER_SIZE:
            return None
        h = HEADER.unpack_from(data, 0)
        return {
            "packet_format": h[0],
            "packet_id": h[5],
            "session_uid": h[6],
            "session_time": h[7],
            "frame_id": h[8],
            "player_car_index": h[10],
        }

    def track_name(self, track_id):
        return self.track_names.get(track_id, f"track_{track_id}")

    def session_type_code(self, session_type):
        return self.session_type_codes.get(session_type, f"S{session_type}")

    def parse(self, data):
        """Raw datagram -> (kind, header, payload), or None.

        None means the packet was too short, was sent in another format, or is of
        no interest to this app. Callers just skip it.
        """
        header = self.parse_header(data)
        if header is None:
            return None
        # Reject other formats outright. Packet sizes overlap between formats - a
        # 2026 Participants packet would sail through a 2025 length check - so
        # without this a mis-set in-game "UDP Format" would yield plausible
        # garbage instead of failing.
        if header["packet_format"] != self.packet_format:
            return None
        handler = self._handlers.get(header["packet_id"])
        if handler is None:
            return None
        kind, parse = handler
        payload = parse(data)
        if payload is None:
            return None
        return kind, header, payload

    # --- per-packet hooks -----------------------------------------------------
    # The unpacked field order of every struct read here is the same in 2025 and
    # 2026, so a subclass only has to restate the layouts, not these methods.

    def _unpack_cars(self, data, layout, offset=HEADER_SIZE):
        if len(data) < offset + self.num_cars * layout.size:
            return None
        return [layout.unpack_from(data, offset + i * layout.size) for i in range(self.num_cars)]

    def _parse_motion(self, data):
        cars = self._unpack_cars(data, self.car_motion)
        if cars is None:
            return None
        return [{"world_x": m[0], "world_z": m[2]} for m in cars]

    def _parse_car_telemetry(self, data):
        cars = self._unpack_cars(data, self.car_telemetry)
        if cars is None:
            return None
        return [{
            "speed": t[0],
            "throttle": t[1] * 100.0,
            "steer": t[2] * 100.0,
            "brake": t[3] * 100.0,
            "gear": t[5],
            # m_drs, 0 = off, 1 = on. Only meaningful for pre-2026 cars; 2026 cars
            # report their wing state as active aero in Car Telemetry 2.
            "drs": t[7],
            "tire_surface_temp": (t[14], t[15], t[16], t[17]),
            "tire_inner_temp": (t[18], t[19], t[20], t[21]),
        } for t in cars]

    def _parse_lap(self, data):
        cars = self._unpack_cars(data, self.lap_data)
        if cars is None:
            return None
        return [{
            # m_lastLapTimeInMS - game-authoritative final time of the previous
            # lap, set the moment S/F is crossed.
            "last_lap_time_ms": l[0],
            "lap_time_ms": l[1],
            # Sector times come split as (msPart:H, minutesPart:B) to support
            # >65s sectors. Combine to a single ms value. Each is 0 until the car
            # crosses that sector's boundary, then latched.
            "sector1_time_ms": l[3] * 60000 + l[2],
            "sector2_time_ms": l[5] * 60000 + l[4],
            "lap_distance": l[10],
            "lap_num": l[14],
            "pit_status": l[15],
            # 0=S1, 1=S2, 2=S3. Used downstream to identify the lap_distance at
            # which the car crossed each sector boundary.
            "sector_idx": l[17],
        } for l in cars]

    def _parse_car_status(self, data):
        cars = self._unpack_cars(data, self.car_status)
        if cars is None:
            return None
        return [{
            "fuel_in_tank": s[5],
            # m_ersStoreEnergy in Joules. Kept raw: the store's capacity is a
            # regulation detail the viewer owns, not something to bake into CSVs.
            "ers_store": s[19],
            # Raw uint8 0..3, see ERS_MODES.
            "ers_mode": s[20],
        } for s in cars]

    def _parse_car_damage(self, data):
        cars = self._unpack_cars(data, self.car_damage)
        if cars is None:
            return None
        # m_tyresWear as percentages.
        return [{"tire_wear": (d[0], d[1], d[2], d[3])} for d in cars]

    def _parse_car_telemetry2(self, data):
        cars = self._unpack_cars(data, self.car_telemetry2)
        if cars is None:
            return None
        return [{
            "regs": REGS_2026 if t[6] else REGS_2025,
            # 0 = corner mode, 1 = straight (low-drag) mode.
            "active_aero_mode": t[0],
            "overtake_active": t[4],
        } for t in cars]

    def _parse_session(self, data):
        if len(data) < HEADER_SIZE + SESSION_PREFIX.size:
            return None
        s = SESSION_PREFIX.unpack_from(data, HEADER_SIZE)
        return {
            "session_type": s[5],
            "session_type_code": self.session_type_code(s[5]),
            "track_id": s[6],
            "track_name": self.track_name(s[6]),
        }

    def _parse_participants(self, data):
        cars = self._unpack_cars(data, self.participant, HEADER_SIZE + 1)  # skip m_numActiveCars
        if cars is None:
            return None
        return [{
            "name": p[7].split(b"\x00", 1)[0].decode("utf-8", errors="replace").strip(),
            "race_number": p[5],
            # 0 = restricted (game zeroes Motion + Car Telemetry for this driver),
            # 1 = public.
            "your_telemetry": p[8],
        } for p in cars]
