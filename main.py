"""F1 25 telemetry recorder — console app.

Single-threaded: blocks on UDP recv, dispatches by packet ID to a parser,
feeds results to the CSV recorder. Stop with Ctrl+C.

Config (`settings.json` next to main.py, or next to the frozen exe):
    {"udp_port": 20777, "output_dir": "recordings"}
"""

import json
import os
import socket
import struct
import sys

import packets
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
    output_dir = settings.get("output_dir", "recordings")
    if not os.path.isabs(output_dir):
        output_dir = os.path.join(_base_dir(), output_dir)

    recorder = Recorder(output_dir)

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1 << 20)
    # Short timeout so Ctrl+C is responsive on Windows, where signal delivery
    # does not interrupt a blocked recvfrom.
    sock.settimeout(0.5)
    sock.bind(("0.0.0.0", udp_port))
    print(f"[telemetro] listening on 0.0.0.0:{udp_port} → {output_dir}", flush=True)

    try:
        while True:
            try:
                data, _ = sock.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError:
                break

            if len(data) < packets.HEADER_SIZE:
                continue
            try:
                header = packets.parse_header(data)
            except struct.error:
                continue

            pid = header["packet_id"]
            try:
                if pid == packets.PACKET_CAR_TELEMETRY:
                    recorder.on_car_telemetry(header, packets.parse_car_telemetry(data))
                elif pid == packets.PACKET_MOTION:
                    recorder.on_motion(header, packets.parse_motion(data))
                elif pid == packets.PACKET_LAP:
                    recorder.on_lap(header, packets.parse_lap(data))
                elif pid == packets.PACKET_CAR_STATUS:
                    recorder.on_car_status(header, packets.parse_car_status(data))
                elif pid == packets.PACKET_CAR_DAMAGE:
                    recorder.on_car_damage(header, packets.parse_car_damage(data))
                elif pid == packets.PACKET_PARTICIPANTS:
                    recorder.on_participants(packets.parse_participants(data))
                elif pid == packets.PACKET_SESSION:
                    recorder.on_session(packets.parse_session(data))
            except Exception as e:
                print(f"[telemetro] parse error packet_id={pid}: {e}", file=sys.stderr, flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        try:
            sock.close()
        except OSError:
            pass
        recorder.close()
        print("[telemetro] stopped.", flush=True)


if __name__ == "__main__":
    main()
