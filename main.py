"""F1 telemetry recorder — console app.

Single-threaded: blocks on UDP recv, hands each datagram to the telemetry parser
for the active wire format, feeds results to the CSV recorder. Stop with Ctrl+C.

The game can emit either the UDP 2025 or the UDP 2026 wire format; settings.json
picks one, or "auto" to identify it from the first packet. All knowledge of the
wire formats lives in the telemetry package — this module never sees a byte
offset or a numeric packet id.

Config (`settings.json` next to main.py, or next to the frozen exe):
    {"udp_port": 20777, "udp_format": "auto", "output_dir": "recordings"}
"""

import json
import os
import socket
import sys

import telemetry
from recorder import Recorder


def _base_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def _load_settings():
    path = os.path.join(_base_dir(), "settings.json")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def main():
    settings = _load_settings()
    udp_port = int(settings.get("udp_port", 20777))
    udp_format = str(settings.get("udp_format", telemetry.AUTO)).strip().lower()
    output_dir = settings.get("output_dir", "recordings")
    if not os.path.isabs(output_dir):
        output_dir = os.path.join(_base_dir(), output_dir)

    # Validated before the socket is bound so a bad value fails immediately.
    try:
        fmt = telemetry.get(udp_format)
    except ValueError as e:
        raise SystemExit(f"settings.json: {e}")

    # None until the format is known: under "auto" that is the first packet that
    # identifies itself, so the per-car arrays cannot be sized earlier.
    recorder = None
    warned_mismatch = False

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1 << 20)
    # Short timeout so Ctrl+C is responsive on Windows, where signal delivery
    # does not interrupt a blocked recvfrom.
    sock.settimeout(0.5)
    sock.bind(("0.0.0.0", udp_port))
    print(f"[telemetro] listening on 0.0.0.0:{udp_port} (udp_format: {udp_format}) "
          f"→ {output_dir}", flush=True)

    try:
        while True:
            try:
                data, _ = sock.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError:
                break

            if fmt is None:
                fmt = telemetry.detect(data)
                if fmt is None:
                    continue
                print(f"[telemetro] detected UDP {fmt.name} format", flush=True)
            if recorder is None:
                recorder = Recorder(output_dir, fmt.num_cars, fmt.regs)

            parsed = fmt.parse(data)
            if parsed is None:
                # A packet the active parser rejected. If it is valid in another
                # registered format then the game is not sending what we are
                # reading and nothing will ever be recorded — say so once. In
                # auto mode that means the format changed after we locked on, so
                # the fix is a restart rather than a settings edit.
                if not warned_mismatch:
                    other = telemetry.detect(data)
                    if other is not None and other is not fmt:
                        if udp_format == telemetry.AUTO:
                            hint = "restart to pick up the change"
                        else:
                            hint = f'set "udp_format" to "{other.name}" or "auto"'
                        print(f"[telemetro] warning: receiving UDP {other.name} packets "
                              f"but reading {fmt.name}; {hint}", file=sys.stderr, flush=True)
                        warned_mismatch = True
                continue

            kind, header, payload = parsed
            try:
                if kind == telemetry.KIND_CAR_TELEMETRY:
                    recorder.on_car_telemetry(header, payload)
                elif kind == telemetry.KIND_MOTION:
                    recorder.on_motion(header, payload)
                elif kind == telemetry.KIND_LAP:
                    recorder.on_lap(header, payload)
                elif kind == telemetry.KIND_CAR_STATUS:
                    recorder.on_car_status(header, payload)
                elif kind == telemetry.KIND_CAR_DAMAGE:
                    recorder.on_car_damage(header, payload)
                elif kind == telemetry.KIND_CAR_TELEMETRY2:
                    recorder.on_car_telemetry2(header, payload)
                elif kind == telemetry.KIND_PARTICIPANTS:
                    recorder.on_participants(payload)
                elif kind == telemetry.KIND_SESSION:
                    recorder.on_session(payload)
            except Exception as e:
                print(f"[telemetro] error handling {kind} packet: {e}",
                      file=sys.stderr, flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        try:
            sock.close()
        except OSError:
            pass
        if recorder is not None:
            recorder.close()
        print("[telemetro] stopped.", flush=True)


if __name__ == "__main__":
    main()
