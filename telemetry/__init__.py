"""Telemetry parser registry.

Registering a new wire format is two steps: add a module beside this one that
subclasses TelemetryFormat, then add its FORMAT to the tuple below. The
settings.json vocabulary, auto-detection and the dispatch loop all follow from
the registry.
"""

from .base import (
    ERS_MODES,
    HEADER_SIZE,
    KIND_CAR_DAMAGE,
    KIND_CAR_STATUS,
    KIND_CAR_TELEMETRY,
    KIND_CAR_TELEMETRY2,
    KIND_LAP,
    KIND_MOTION,
    KIND_PARTICIPANTS,
    KIND_SESSION,
    REGS_2025,
    REGS_2026,
    TelemetryFormat,
)
from .f1_2025 import FORMAT as F1_2025
from .f1_2026 import FORMAT as F1_2026

AUTO = "auto"

FORMATS = {f.name: f for f in (F1_2025, F1_2026)}
_BY_PACKET_FORMAT = {f.packet_format: f for f in FORMATS.values()}

CHOICES = sorted(FORMATS) + [AUTO]


def get(name):
    """Resolve a settings.json "udp_format" value to a format.

    Returns None for "auto", meaning the caller should feed packets to detect()
    until one identifies itself. Raises ValueError on anything unrecognised.
    """
    key = str(name).strip().lower()
    if key == AUTO:
        return None
    if key not in FORMATS:
        raise ValueError(f"unknown udp_format {name!r}; expected one of {', '.join(CHOICES)}")
    return FORMATS[key]


def detect(data):
    """Identify a datagram's format from its header, or None if unrecognised."""
    header = TelemetryFormat.parse_header(data)
    if header is None:
        return None
    return _BY_PACKET_FORMAT.get(header["packet_format"])
