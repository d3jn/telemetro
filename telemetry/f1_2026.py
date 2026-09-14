"""F1 25 UDP 2026 format, as shipped with the 2026 Season Pack.

Reference: "2026 Season Pack Telemetry Output Structures.txt" and "Data Output
from F1 25 2026 Season Pack.pdf", as kept in f1-race-control/specs/25vs26.

What changed in the structs this app reads:

  * every car array grew from 22 to 24 entries, for the new eleventh team;
  * CarMotionData quantised its g-force components to int16;
  * ParticipantData widened driverId, networkId and teamId to uint16;
  * CarTelemetryData narrowed engineTemperature to uint8;
  * CarStatusData gained m_ersHarvestLimitPerLap after the MGU-H harvest;
  * the new Car Telemetry 2 packet carries active aero, Overtake Mode and the
    per-car "2026 regulations" flag.

The unpacked index of every field read here is unchanged, so only the layouts
differ from 2025. LapData, CarDamageData and the session prefix are identical.
"""

import struct

from . import f1_2025

NUM_CARS = 24

# CarMotionData - 54 bytes: 3f position, 3f velocity, 3h forward, 3h right,
# 3h g-force (quantised), 3f yaw/pitch/roll. Packet: 29 + 24 * 54 = 1325 bytes.
CAR_MOTION = struct.Struct("<6f9h3f")

# Packet: 29 + 24 * 57 + 2 = 1399 bytes.
LAP_DATA = f1_2025.LAP_DATA

# ParticipantData - 60 bytes: aiControlled (uint8), driverId, networkId, teamId
# (uint16 each), myTeam, raceNumber, nationality, name[32], yourTelemetry,
# showOnlineNames, techLevel, platform, numColours, 4 x RGB livery.
# Packet: 29 + 1 + 24 * 60 = 1470 bytes.
PARTICIPANT = struct.Struct("<B3H3B32s2BH2B12B")

# CarTelemetryData - 59 bytes: as 2025 but engineTemperature is uint8.
# Packet: 29 + 24 * 59 + 3 = 1448 bytes.
CAR_TELEMETRY = struct.Struct("<HfffBbHBBH4H4B4BB4f4B")

# CarStatusData - 59 bytes: as 2025 plus m_ersHarvestLimitPerLap (float) after
# m_ersHarvestedThisLapMGUH. Packet: 29 + 24 * 59 = 1445 bytes.
CAR_STATUS = struct.Struct("<BBBBBfffHHBBHBBBbfffBffffB")

# Packet: 29 + 24 * 46 = 1133 bytes.
CAR_DAMAGE = f1_2025.CAR_DAMAGE

# CarTelemetry2Data - 10 bytes: activeAeroMode, activeAeroAvailable (uint8),
# activeAeroActivationDistance (uint16), overtakeAvailable, overtakeActive
# (uint8), overtakeActivationDistance (uint16), 2026Regulations, drivingWrongWay
# (uint8). Packet: 29 + 24 * 10 = 269 bytes.
CAR_TELEMETRY2 = struct.Struct("<BBHBBHBB")

# Appendix "Track IDs": the 2025 table plus Madrid.
TRACK_NAMES = dict(f1_2025.TRACK_NAMES)
TRACK_NAMES.update({
    42: "madrid",
})

SESSION_TYPE_CODES = f1_2025.SESSION_TYPE_CODES


class F1_2026Format(f1_2025.F1_2025Format):
    name = "2026"
    packet_format = 2026
    num_cars = NUM_CARS
    # Pre-2026 cars can still be driven under this format, so regs are per car.
    regs = None

    track_names = TRACK_NAMES
    session_type_codes = SESSION_TYPE_CODES

    car_motion = CAR_MOTION
    lap_data = LAP_DATA
    participant = PARTICIPANT
    car_telemetry = CAR_TELEMETRY
    car_status = CAR_STATUS
    car_damage = CAR_DAMAGE
    car_telemetry2 = CAR_TELEMETRY2


FORMAT = F1_2026Format()
