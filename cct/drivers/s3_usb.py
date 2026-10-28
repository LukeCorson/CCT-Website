"""Direct USB control for the six-relay S3/Nano output firmware.

The browser exposes Valve 1..6, never a raw mask. The wire frame remains the
three-byte protocol supplied with the controller firmware.
"""

from collections import deque
import queue
import threading
import time

COMMAND_HEADER = 0xA5
REPLY_HEADER = 0x5A
RELAY_COUNT = 6
ALLOWED_VALVES = tuple(f"valve_{number}" for number in range(1, RELAY_COUNT + 1))


def command_frame(mask):
    if type(mask) is not int or not 0 <= mask <= 0x3F:
        raise ValueError("Relay state must contain exactly six bits.")
    return bytes((COMMAND_HEADER, mask, COMMAND_HEADER ^ mask))


class ReplyParser:
    def __init__(self):
        self.buffer = bytearray()

    def feed(self, data):
        replies = []
        self.buffer.extend(data)
        while self.buffer:
            try:
                start = self.buffer.index(REPLY_HEADER)
            except ValueError:
                self.buffer.clear()
                break
            if start:
                del self.buffer[:start]
            if len(self.buffer) < 4:
                break
            header, state, counter, checksum = self.buffer[:4]
            del self.buffer[:4]
            if checksum == (header ^ state ^ counter) and state <= 0x3F:
                replies.append({"state": state, "counter": counter})
        return replies


def valves_to_mask(valves):
    if not isinstance(valves, dict) or set(valves) != set(ALLOWED_VALVES):
        raise ValueError("Provide on/off state for Valve 1 through Valve 6.")
    mask = 0
    # Firmware bit 5 is Valve 1 and bit 0 is Valve 6.
    for number in range(1, RELAY_COUNT + 1):
        value = valves[f"valve_{number}"]
        if type(value) is not bool:
            raise ValueError("Every valve state must be true or false.")
        if value:
            mask |= 1 << (RELAY_COUNT - number)
    return mask


def mask_to_valves(mask):
    return {f"valve_{number}": bool(mask & (1 << (RELAY_COUNT - number)))
            for number in range(1, RELAY_COUNT + 1)}


class S3UsbLink:
    """One worker owns the serial port and maintains the firmware watchdog."""

    def __init__(self, port, baud=115200, serial_factory=None):
        if not isinstance(port, str) or not port.strip():
            raise ValueError("Choose the S3 USB serial port.")
        self.port = port.strip()
        self.baud = int(baud)
        self.serial_factory = serial_factory
        self.serial = None
        self._commands = queue.Queue(maxsize=8)
        self._stop = threading.Event()
        self._thread = None
        self._lock = threading.RLock()
        self._parser = ReplyParser()
        self._events = deque(maxlen=100)
        self._desired = 0
        self._applied = None
        self._reply_counter = None
        self._commanding = False
        self._error = ""
        self._last_reply = None

    def connect(self):
        if self.serial is not None:
            return
        if self.serial_factory is None:
            import serial
            self.serial = serial.Serial(port=self.port, baudrate=self.baud, timeout=0.05,
                                        write_timeout=1, exclusive=True)
        else:
            self.serial = self.serial_factory(self.port, self.baud)
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="cct-direct-s3", daemon=True)
        self._thread.start()

    def close(self):
        if self.serial is None:
            return
        if self._commanding:
            try:
                self.set_valves({name: False for name in ALLOWED_VALVES}, timeout=1.5)
            except (RuntimeError, OSError, TimeoutError):
                pass
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
        try:
            self.serial.close()
        finally:
            self.serial = None

    def set_valves(self, valves, timeout=2.0):
        mask = valves_to_mask(valves)
        done = threading.Event()
        result = {}
        try:
            self._commands.put_nowait((mask, done, result))
        except queue.Full as error:
            raise RuntimeError("The S3 command queue is busy.") from error
        if not done.wait(timeout):
            raise TimeoutError("S3 did not confirm the relay state over USB.")
        if result.get("error"):
            raise RuntimeError(result["error"])
        return self.snapshot()

    def all_off(self):
        return self.set_valves({name: False for name in ALLOWED_VALVES})

    def snapshot(self):
        with self._lock:
            age = None if self._last_reply is None else time.monotonic() - self._last_reply
            return {"connected": self.serial is not None, "fresh": age is not None and age <= 2,
                    "message": self._error or ("USB port open; no relay command sent" if not self._commanding else
                    "Relay state confirmed" if age is not None and age <= 2 else "Waiting for S3 reply"),
                    "port": self.port, "desired_valves": mask_to_valves(self._desired),
                    "applied_valves": mask_to_valves(self._applied) if self._applied is not None else None,
                    "reply_counter": self._reply_counter, "reply_age_s": age,
                    "heating_available": False}

    def drain_events(self):
        with self._lock:
            events = list(self._events)
            self._events.clear()
            return events

    def _exchange(self, mask, timeout=1.0):
        before = self._reply_counter
        self.serial.write(command_frame(mask))
        self.serial.flush()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and not self._stop.is_set():
            waiting = getattr(self.serial, "in_waiting", 0)
            data = self.serial.read(max(1, min(waiting, 64)))
            for reply in self._parser.feed(data):
                with self._lock:
                    self._applied = reply["state"]
                    self._reply_counter = reply["counter"]
                    self._last_reply = time.monotonic()
                if reply["counter"] != before and reply["state"] == mask:
                    return
        raise TimeoutError("No matching S3 USB acknowledgement.")

    def _run(self):
        next_heartbeat = time.monotonic()
        while not self._stop.is_set():
            try:
                mask, done, result = self._commands.get(timeout=0.1)
            except queue.Empty:
                if not self._commanding or time.monotonic() < next_heartbeat:
                    continue
                mask = self._desired
                done = result = None
            try:
                self._exchange(mask)
                with self._lock:
                    self._desired = mask
                    self._commanding = True
                    self._error = ""
                    self._events.append({"event": "s3_relay_ack", "message": "S3 relay state confirmed",
                                         "valves": mask_to_valves(mask)})
            except (OSError, TimeoutError) as error:
                with self._lock:
                    self._error = str(error)
                    self._events.append({"event": "s3_relay_error", "message": str(error)})
                if result is not None:
                    result["error"] = str(error)
            finally:
                next_heartbeat = time.monotonic() + 0.5
                if done is not None:
                    done.set()
