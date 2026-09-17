"""USB source adapter for TerraVeil HOST-01.

Drop-in replacement focused on reliable REAL HARDWARE operation on both Windows
and WSL/Linux.

Key behaviours:
- Auto-detects Silicon Labs CP210x (VID:PID 10c4:ea60).
- Falls back to /dev/ttyUSB* or /dev/ttyACM* in WSL/Linux.
- Supports current HUB_DATA lines, buffered TELEMETRY JSON, and legacy RAW blocks.
- Marks the transport type so telemetry.py can safely distinguish a fresh live
  sequence reset from replayed buffered data.
"""

import glob
import json
import logging
import math
import os
import queue
import re
import sqlite3
import threading
import time
from datetime import datetime, timezone

log = logging.getLogger(__name__)

HOST_ID = "HOST-01"
DEFAULT_ZONE_ID = "ZONE-A"

SUPPORTED_NODES = {
    "UG-01": "UG",
    "UG-02": "UG",
    "LD-01": "LD",
}

CP210X_VID = 0x10C4
CP210X_PID = 0xEA60


def detect_serial_port(configured=None):
    """Resolve HOST-01 serial port across Windows and WSL/Linux.

    An explicitly configured TERRAVEIL_SERIAL_PORT always wins. Use "auto" (or
    omit the variable) to discover the CP210x automatically.
    """
    configured = (configured or "auto").strip()
    if configured and configured.lower() != "auto":
        return configured

    try:
        from serial.tools import list_ports

        ports = list(list_ports.comports())

        # Strongest match: exact CP210x VID:PID used by HOST-01.
        for port in ports:
            if port.vid == CP210X_VID and port.pid == CP210X_PID:
                return port.device

        # Descriptive fallback for drivers/platforms that omit VID/PID metadata.
        for port in ports:
            text = " ".join(
                str(value or "")
                for value in (
                    port.device,
                    port.description,
                    port.manufacturer,
                    port.product,
                    port.hwid,
                )
            ).lower()
            if "cp210" in text or "silicon labs" in text:
                return port.device

        # On Linux/WSL prefer USB UART/ACM ports over unrelated serial devices.
        for port in ports:
            device = str(port.device or "")
            if device.startswith("/dev/ttyUSB") or device.startswith("/dev/ttyACM"):
                return device

    except Exception as error:
        log.debug("Serial port discovery via pyserial failed: %s", error)

    # WSL/Linux fallback even if pyserial cannot enumerate metadata yet.
    candidates = sorted(glob.glob("/dev/ttyUSB*")) + sorted(glob.glob("/dev/ttyACM*"))
    if candidates:
        return candidates[0]

    # No device is currently attached. Return None so the read loop keeps
    # retrying discovery instead of hard-wiring the wrong platform path.
    return None


class ReceiverParser:
    def __init__(self):
        self.pending = None
        self.started = 0

    @staticmethod
    def _finite_float(value):
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("non-finite number")
        return number

    def _parse_hub_data(self, line):
        parts = [part.strip() for part in line.split(",")]
        if not parts or parts[0] != "HUB_DATA":
            return None

        fields = {}
        for item in parts[1:]:
            key, sep, value = item.partition("=")
            if not sep or not key or value == "":
                return None
            fields[key.strip().upper()] = value.strip()

        node_id = fields.get("NODE")
        node_type = fields.get("TYPE")
        if node_id not in SUPPORTED_NODES or node_type != SUPPORTED_NODES[node_id]:
            return None

        sequence_text = fields.get("SEQ", "")
        if not re.fullmatch(r"\d{1,10}", sequence_text):
            return None
        sequence = int(sequence_text)
        if not 0 <= sequence <= 4294967295:
            return None

        try:
            payload = {
                "node_id": node_id,
                "host_id": HOST_ID,
                "zone_id": DEFAULT_ZONE_ID,
                "sequence": sequence,
                "rssi": self._finite_float(fields["RSSI"]),
                "snr": self._finite_float(fields["SNR"]),
                "_transport": "HUB_DATA",
            }

            if node_type == "UG":
                vibration = self._finite_float(fields["VIBRATION"])
                if vibration not in (0.0, 1.0):
                    return None
                payload.update(
                    roll=self._finite_float(fields["ROLL"]),
                    pitch=self._finite_float(fields["PITCH"]),
                    vibration=int(vibration),
                    soil=self._finite_float(fields["SOIL_ADC"]),
                )
            else:  # LD
                payload.update(
                    displacement_mm=self._finite_float(fields["DISP_MM"]),
                    potentiometer_raw=self._finite_float(fields["POT_ADC"]),
                )
                if "ROLL" in fields:
                    payload["roll"] = self._finite_float(fields["ROLL"])
                if "PITCH" in fields:
                    payload["pitch"] = self._finite_float(fields["PITCH"])

            return payload, False
        except (KeyError, ValueError, TypeError):
            return None

    def feed(self, line, now=None):
        now = time.monotonic() if now is None else now
        line = line.strip()

        if self.pending and now - self.started > 5:
            self.pending = None

        if line.startswith("HUB_DATA,"):
            self.pending = None
            return self._parse_hub_data(line)

        if line.startswith("TELEMETRY "):
            self.pending = None
            try:
                payload = json.loads(line[10:])
                if not isinstance(payload, dict):
                    return None
                payload = dict(payload)
                payload["_transport"] = "BUFFERED"
                return payload, True
            except (ValueError, TypeError):
                return None

        if line.startswith("RAW:"):
            self.pending = None
            parts = line[4:].strip().split(",")
            if (
                len(parts) != 7
                or parts[0] != "DATA"
                or parts[1] not in ("UG-01", "UG-02")
                or not re.fullmatch(r"\d{1,10}", parts[2])
            ):
                return None
            try:
                values = [float(v) for v in parts[3:]]
                if not all(math.isfinite(v) for v in values):
                    return None
                self.pending = dict(
                    node_id=parts[1],
                    sequence=int(parts[2]),
                    roll=values[0],
                    pitch=values[1],
                    vibration=values[2],
                    soil=values[3],
                    _transport="LEGACY",
                )
                self.started = now
            except ValueError:
                return None

        elif self.pending:
            fields = {"Host ID": "host_id", "Zone ID": "zone_id"}
            label, separator, value = line.partition(":")
            if separator and label.strip() in fields:
                self.pending[fields[label.strip()]] = value.strip()
            elif separator and label.strip() in ("RSSI", "SNR"):
                unit = "dBm" if label.strip() == "RSSI" else "dB"
                match = re.fullmatch(
                    r"([+-]?\d+(?:\.\d+)?)\s*" + unit, value.strip()
                )
                if match:
                    self.pending[label.strip().lower()] = float(match[1])
            elif line == "[OK] Packet processed.":
                packet, self.pending = self.pending, None
                if all(k in packet for k in ("host_id", "zone_id", "rssi", "snr")):
                    packet["queue_age_ms"] = max(0, int((now - self.started) * 1000))
                    return packet, False
        return None


class LineDecoder:
    def __init__(self):
        self.buffer = bytearray()
        self.discard = False

    def feed(self, data):
        lines = []
        for byte in data:
            if byte == 10:
                if not self.discard:
                    lines.append(self.buffer.decode("utf-8", errors="replace"))
                self.buffer.clear()
                self.discard = False
            elif not self.discard:
                if len(self.buffer) >= 2048:
                    self.buffer.clear()
                    self.discard = True
                else:
                    self.buffer.append(byte)
        return lines


class SerialBridge:
    def __init__(self, ingest, port="auto", baudrate=115200):
        self.ingest = ingest
        self.configured_port = port or "auto"
        self.port_name = None
        self.baudrate = baudrate
        self.stop_event = threading.Event()
        self.pending = queue.Queue(maxsize=256)
        self.port = None
        self.connection_lock = threading.Lock()
        self.status = "STARTING"
        self.last_error = None
        self.received = 0
        self.applied = 0
        self.duplicates = 0
        self.historical = 0
        self.rejected = 0
        self.last_sequence = None
        self.last_node = None
        self.last_received_at = None
        self.last_result = None
        self.stored = 0
        self.dropped = 0
        self.inflight = set()
        self.inflight_lock = threading.Lock()
        self.threads = []

    def snapshot(self):
        return dict(
            transport="USB SERIAL",
            configured_port=self.configured_port,
            port=self.port_name or self.configured_port,
            status=self.status,
            last_error=self.last_error,
            received=self.received,
            applied=self.applied,
            duplicates=self.duplicates,
            historical=self.historical,
            rejected=self.rejected,
            last_node=self.last_node,
            last_sequence=self.last_sequence,
            last_received_at=self.last_received_at,
            last_result=self.last_result,
            queued=self.pending.qsize(),
            stored=self.stored,
            dropped=self.dropped,
        )

    def start(self):
        for target in (self.read_loop, self.store_loop):
            thread = threading.Thread(
                target=target,
                daemon=True,
                name="terraveil-usb-" + target.__name__,
            )
            thread.start()
            self.threads.append(thread)
        return self

    def stop(self):
        self.stop_event.set()
        for thread in self.threads:
            thread.join(timeout=2)

    def enqueue(self, parsed, observed=None):
        payload, acknowledgement = parsed
        node, sequence = payload.get("node_id"), payload.get("sequence")
        if (
            node not in SUPPORTED_NODES
            or type(sequence) is not int
            or not 0 <= sequence <= 4294967295
        ):
            return

        self.received += 1
        self.last_node = node
        self.last_sequence = sequence
        self.last_received_at = datetime.now(timezone.utc).isoformat()
        key = node, sequence

        with self.inflight_lock:
            if key in self.inflight:
                return
            self.inflight.add(key)

        try:
            self.pending.put_nowait(
                (
                    payload,
                    acknowledgement,
                    time.monotonic() if observed is None else observed,
                    key,
                )
            )
        except queue.Full:
            with self.inflight_lock:
                self.inflight.discard(key)
            self.dropped += 1
            log.error("USB pending queue full; packet %s dropped (total %s)", key, self.dropped)

    def _resolve_port(self):
        port = detect_serial_port(self.configured_port)
        self.port_name = port
        return port

    def read_loop(self):
        import serial

        while not self.stop_event.is_set():
            port = None
            try:
                resolved = self._resolve_port()
                if not resolved:
                    self.status = "DISCONNECTED"
                    self.last_error = "HOST-01 CP210x not visible to this OS/WSL instance"
                    self.stop_event.wait(2)
                    continue

                port = serial.Serial(
                    port=None,
                    baudrate=self.baudrate,
                    timeout=0.3,
                    write_timeout=0.3,
                )
                port.dtr = False
                port.rts = False
                port.port = resolved
                port.open()
                port.reset_input_buffer()

                with self.connection_lock:
                    self.port = port

                self.status, self.last_error = "OPEN", None
                log.info("USB receiver connected on %s at %s baud", resolved, self.baudrate)
                parser, decoder = ReceiverParser(), LineDecoder()

                while not self.stop_event.is_set():
                    chunk = port.read(min(max(port.in_waiting, 1), 4096))
                    for line in decoder.feed(chunk):
                        parsed = parser.feed(line)
                        if parsed:
                            self.enqueue(parsed)

            except (serial.SerialException, OSError) as error:
                message = str(error)
                if message != self.last_error:
                    log.warning("USB receiver unavailable on %s: %s", self.port_name, message)
                self.status, self.last_error = (
                    "BUSY" if "Access is denied" in message else "DISCONNECTED",
                    message,
                )
            finally:
                with self.connection_lock:
                    self.port = None
                    if port and port.is_open:
                        port.close()

            self.stop_event.wait(2)

        self.status = "STOPPED"

    def deliver(self, item):
        payload, acknowledgement, observed, key = item
        value = payload.get("queue_age_ms", 0)
        if type(value) is not int or not 0 <= value <= 31536000000:
            raise ValueError("Invalid gateway queue age")

        payload = dict(
            payload,
            queue_age_ms=value + max(0, int((time.monotonic() - observed) * 1000)),
        )
        result = self.ingest(payload)
        if not result.get("success"):
            raise RuntimeError("Ingestion did not confirm storage")

        if acknowledgement:
            with self.connection_lock:
                if self.port and self.port.is_open:
                    try:
                        self.port.write(f"STORED,{key[0]},{key[1]}\n".encode("ascii"))
                    except (OSError, TimeoutError):
                        pass

        if result.get("new_session"):
            self.applied += 1
            self.last_result = "LIVE UPDATE: node reboot detected; sequence session renewed"
        elif result.get("duplicate"):
            self.duplicates += 1
            self.last_result = "DUPLICATE: already stored in this node session"
        elif result.get("applied") is False:
            self.historical += 1
            self.last_result = (
                "OLDER SEQUENCE: saved to history, not live state. "
                "Buffered/replayed packets do not refresh node freshness."
            )
        else:
            self.applied += 1
            self.last_result = "LIVE UPDATE"

        if not result.get("duplicate"):
            self.stored += 1
            log.info("USB stored %s sequence=%s", *key)
        return result

    def store_loop(self):
        while not self.stop_event.is_set():
            try:
                item = self.pending.get(timeout=0.3)
            except queue.Empty:
                continue

            try:
                while not self.stop_event.is_set():
                    try:
                        self.deliver(item)
                        break
                    except ValueError as error:
                        self.rejected += 1
                        self.last_result = "REJECTED: " + str(error)
                        log.warning("USB rejected telemetry: %s", error)
                        break
                    except (sqlite3.Error, RuntimeError, OSError) as error:
                        log.error("USB storage retry: %s", error)
                        self.stop_event.wait(2)
            finally:
                with self.inflight_lock:
                    self.inflight.discard(item[3])
                self.pending.task_done()


_bridge = None


def start(ingest):
    global _bridge
    if _bridge is None and os.environ.get("TERRAVEIL_SERIAL_ENABLED", "1") == "1":
        _bridge = SerialBridge(
            ingest,
            os.environ.get("TERRAVEIL_SERIAL_PORT", "auto"),
            int(os.environ.get("TERRAVEIL_SERIAL_BAUD", "115200")),
        ).start()
    return _bridge


def health():
    return (
        _bridge.snapshot()
        if _bridge
        else dict(
            transport="USB SERIAL",
            configured_port=os.environ.get("TERRAVEIL_SERIAL_PORT", "auto"),
            port=os.environ.get("TERRAVEIL_SERIAL_PORT", "auto"),
            status="NOT STARTED",
        )
    )
