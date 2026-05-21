"""CSV recorder — one append-mode file per driver per session.

File naming: `YYYY_MM_DD_<track>_<driver>_<session_type>.csv`. If the file
already exists (app restart, same session ID, same driver) we append; if not
we create + write a header.

All filesystem operations are wrapped: any OSError is logged and the affected
driver is skipped for the remainder of the session, but the recorder never
raises out of `on_*` so the UDP loop keeps running.

Restricted drivers (m_yourTelemetry == 0) are skipped — the game zeroes their
Motion + Car Telemetry, so we cannot tell "parked at origin" from "no data".
"""

import csv
import os
import re
import sys
from datetime import datetime


ROW_FIELDS = [
    "lap_num",
    "lap_run",
    "brake",
    "throttle",
    "steer",
    "gear",
    "speed",
    "ers_pct",
    "ers_mode",
    "fuel_level",
    "tire_wear_rl",
    "tire_wear_rr",
    "tire_wear_fl",
    "tire_wear_fr",
    "tire_temp_surface_rl",
    "tire_temp_surface_rr",
    "tire_temp_surface_fl",
    "tire_temp_surface_fr",
    "tire_temp_inner_rl",
    "tire_temp_inner_rr",
    "tire_temp_inner_fl",
    "tire_temp_inner_fr",
    "world_x",
    "world_z",
    "lap_time",
    "lap_distance",
    "last_lap_time",
]


# Filesystem-unsafe characters. Windows is the strictest, so we sanitise to
# the Windows-safe set even on Linux.
_UNSAFE_CHAR_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
# Windows reserved device names — illegal as a filename stem regardless of
# extension. Extremely unlikely as an F1 driver name, but cheap to handle.
_WINDOWS_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def _header_matches(path, expected_fields):
    """Read the first CSV line and check it matches ``expected_fields`` exactly.

    Returns False on any read failure — caller treats that the same as a real
    mismatch and rotates the file aside rather than risking a corrupt append.
    """
    try:
        with open(path, "r", encoding="utf-8", newline="") as f:
            header = next(csv.reader(f), None)
    except OSError:
        return False
    return header == list(expected_fields)


def _sanitise_for_filename(name):
    """Replace filesystem-unsafe characters and collapse whitespace.

    Empty / all-stripped input returns "unknown" so the caller always gets a
    non-empty filename component."""
    cleaned = _UNSAFE_CHAR_RE.sub("_", name)
    cleaned = re.sub(r"\s+", "_", cleaned).strip("._")
    if not cleaned:
        return "unknown"
    if cleaned.upper() in _WINDOWS_RESERVED:
        cleaned = f"_{cleaned}"
    return cleaned


class Recorder:
    def __init__(self, output_dir):
        self.output_dir = output_dir
        try:
            os.makedirs(output_dir, exist_ok=True)
        except OSError as e:
            print(f"[recorder] cannot create {output_dir}: {e}",
                  file=sys.stderr, flush=True)

        self.session_uid = 0
        self._session_date = None       # set when a new session UID is observed
        self._track_name = None         # populated by Session packet
        self._session_type_code = None  # populated by Session packet
        self._files = {}                # driver_index -> open file handle
        self._writers = {}              # driver_index -> csv.DictWriter
        self._skip = set()              # drivers we have given up on this session

        self._motion = [None] * 22
        self._lap = [None] * 22
        self._status = [None] * 22
        self._damage = [None] * 22
        self._participants = [None] * 22

        # Per-driver flashback / load-from-save tracking. A regression in
        # (lap_num, lap_distance) means the player rewound time, so the next
        # pass over the same lap needs its own identity — we bump `_lap_run`
        # and stamp it on every emitted row, letting the viewer treat the
        # repeated lap as a separate lap instead of overlaying it.
        self._last_position = [None] * 22
        self._lap_run = [0] * 22
        # Highest frame_id we've accepted per packet kind. UDP can reorder
        # packets — a stale LAP packet would walk lap_distance backwards and
        # mimic a flashback; stale telemetry/motion/status/damage would mix
        # a previous frame's values into the next written row. We gate each
        # ingestion on this so cached state never moves backwards in time.
        self._last_frame_id = {
            "motion": 0,
            "lap": 0,
            "telemetry": 0,
            "status": 0,
            "damage": 0,
        }

    def _close_all(self):
        for f in self._files.values():
            try:
                f.close()
            except OSError:
                pass
        self._files.clear()
        self._writers.clear()
        self._skip.clear()

    def _reset_session(self, session_uid):
        # New session UID = new files. On a real session boundary (UID changed
        # from one real value to another) we also clear track/session-type so
        # we don't mislabel the new session with stale metadata — the next
        # Session packet (sent at 2 Hz) re-populates them. On initialisation
        # (0 -> real UID) we keep whatever metadata was already set.
        had_session = self.session_uid != 0
        self._close_all()
        self.session_uid = session_uid
        self._session_date = datetime.now().strftime("%Y_%m_%d")
        if had_session:
            self._track_name = None
            self._session_type_code = None
        self._last_position = [None] * 22
        self._lap_run = [0] * 22
        self._last_frame_id = {k: 0 for k in self._last_frame_id}

    def _fresh(self, kind, header):
        """Accept-or-drop test for an incoming packet.

        Returns True (and bumps the high-water mark) when the packet is
        from the current session and not older than something we've already
        processed for the same kind. Returns False for cross-session or
        reordered packets so callers can drop them without further work.
        """
        if header["session_uid"] != self.session_uid:
            return False
        frame_id = header["frame_id"]
        if frame_id < self._last_frame_id[kind]:
            return False
        self._last_frame_id[kind] = frame_id
        return True

    def on_session(self, info):
        self._track_name = info["track_name"]
        self._session_type_code = info["session_type_code"]

    def on_motion(self, header, samples):
        if self._fresh("motion", header):
            self._motion = samples

    def on_lap(self, header, samples):
        if self._fresh("lap", header):
            self._lap = samples

    def on_car_status(self, header, samples):
        if self._fresh("status", header):
            self._status = samples

    def on_car_damage(self, header, samples):
        if self._fresh("damage", header):
            self._damage = samples

    def on_participants(self, samples):
        self._participants = samples

    def _writer_for(self, driver_index, driver_name):
        if driver_index in self._writers:
            return self._writers[driver_index]
        if driver_index in self._skip:
            return None
        if not self._session_date or not self._track_name or not self._session_type_code:
            return None

        safe_driver = _sanitise_for_filename(driver_name or f"driver_{driver_index}")
        filename = (
            f"{self._session_date}_{self._track_name}_{safe_driver}_"
            f"{self._session_type_code}.csv"
        )
        path = os.path.join(self.output_dir, filename)

        try:
            existed = os.path.exists(path) and os.path.getsize(path) > 0
        except OSError:
            existed = False  # treat unreadable stat as "new" — open() will fail loudly if it really can't be touched

        if existed and not _header_matches(path, ROW_FIELDS):
            # An older recorder build wrote this file with a different column
            # set. Appending now would interleave row shapes and silently
            # corrupt the CSV, so rotate the old file aside and start fresh.
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            legacy_path = f"{path}.legacy_{stamp}.csv"
            try:
                os.rename(path, legacy_path)
                print(f"[recorder] schema changed, moved old file to {legacy_path}",
                      flush=True)
                existed = False
            except OSError as e:
                print(f"[recorder] schema mismatch on {path} and could not rotate: {e}",
                      file=sys.stderr, flush=True)
                self._skip.add(driver_index)
                return None

        try:
            # Append mode + line buffering: each row is flushed on write, so a
            # crash or Ctrl+C only loses the in-flight row, and re-runs append
            # cleanly without duplicating the header.
            f = open(path, "a", newline="", encoding="utf-8", buffering=1)
        except OSError as e:
            print(f"[recorder] cannot open {path}: {e}",
                  file=sys.stderr, flush=True)
            self._skip.add(driver_index)
            return None

        writer = csv.DictWriter(f, fieldnames=ROW_FIELDS)
        if not existed:
            try:
                writer.writeheader()
            except OSError as e:
                print(f"[recorder] header write failed for {path}: {e}",
                      file=sys.stderr, flush=True)
                try:
                    f.close()
                except OSError:
                    pass
                self._skip.add(driver_index)
                return None

        self._files[driver_index] = f
        self._writers[driver_index] = writer
        print(f"[recorder] {'appending to' if existed else 'opened'} {path}",
              flush=True)
        return writer

    def _drop_driver(self, driver_index):
        f = self._files.pop(driver_index, None)
        self._writers.pop(driver_index, None)
        if f is not None:
            try:
                f.close()
            except OSError:
                pass
        self._skip.add(driver_index)

    def on_car_telemetry(self, header, samples):
        session_uid = header["session_uid"]
        if session_uid == 0:
            return
        if session_uid != self.session_uid:
            self._reset_session(session_uid)
        if not self._track_name or not self._session_type_code:
            return  # waiting for Session packet — usually <0.5 s after start
        # Drop stale telemetry packets so we don't emit a row whose
        # brake/throttle/etc. come from an older frame than the LAP/MOTION
        # state we'd pair them with. Runs *after* the session reset so the
        # first packet of a new session passes (high-water mark is 0).
        if not self._fresh("telemetry", header):
            return

        for i, telem in enumerate(samples):
            participant = self._participants[i]
            if participant is None or not participant["name"]:
                continue
            if participant["your_telemetry"] == 0:
                continue

            writer = self._writer_for(i, participant["name"])
            if writer is None:
                continue

            motion = self._motion[i]
            lap = self._lap[i]
            status = self._status[i]
            damage = self._damage[i]

            if lap is not None:
                lap_num = lap["lap_num"]
                lap_distance = lap["lap_distance"]
                last = self._last_position[i]
                if last is not None:
                    last_num, last_dist = last
                    # Time moved backwards = flashback or load-from-save.
                    # Crossing start/finish also resets lap_distance, but
                    # lap_num bumps up at the same time so it doesn't match.
                    # The 1m slack absorbs minor lap_distance jitter near
                    # the line without missing a real rewind (always tens
                    # of metres at least).
                    if lap_num < last_num or (
                        lap_num == last_num and lap_distance < last_dist - 1.0
                    ):
                        self._lap_run[i] += 1
                self._last_position[i] = (lap_num, lap_distance)

            surface = telem["tire_surface_temp"]
            inner = telem["tire_inner_temp"]
            wear = damage["tire_wear"] if damage else (None, None, None, None)

            row = {
                "lap_num": lap["lap_num"] if lap else "",
                "lap_run": self._lap_run[i],
                "brake": f"{telem['brake']:.2f}",
                "throttle": f"{telem['throttle']:.2f}",
                "steer": f"{telem['steer']:.2f}",
                "gear": telem["gear"],
                "speed": telem["speed"],
                "ers_pct": f"{status['ers_pct']:.2f}" if status else "",
                "ers_mode": status["ers_mode"] if status else "",
                "fuel_level": f"{status['fuel_in_tank']:.3f}" if status else "",
                "tire_wear_rl": f"{wear[0]:.3f}" if wear[0] is not None else "",
                "tire_wear_rr": f"{wear[1]:.3f}" if wear[1] is not None else "",
                "tire_wear_fl": f"{wear[2]:.3f}" if wear[2] is not None else "",
                "tire_wear_fr": f"{wear[3]:.3f}" if wear[3] is not None else "",
                "tire_temp_surface_rl": surface[0],
                "tire_temp_surface_rr": surface[1],
                "tire_temp_surface_fl": surface[2],
                "tire_temp_surface_fr": surface[3],
                "tire_temp_inner_rl": inner[0],
                "tire_temp_inner_rr": inner[1],
                "tire_temp_inner_fl": inner[2],
                "tire_temp_inner_fr": inner[3],
                "world_x": f"{motion['world_x']:.3f}" if motion else "",
                "world_z": f"{motion['world_z']:.3f}" if motion else "",
                "lap_time": lap["lap_time_ms"] if lap else "",
                "lap_distance": f"{lap['lap_distance']:.2f}" if lap else "",
                "last_lap_time": lap["last_lap_time_ms"] if lap else "",
            }
            try:
                writer.writerow(row)
            except OSError as e:
                print(f"[recorder] write failed for driver {i}: {e}",
                      file=sys.stderr, flush=True)
                self._drop_driver(i)

    def close(self):
        self._close_all()
