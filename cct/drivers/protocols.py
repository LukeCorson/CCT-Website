"""Bounded, strict wire parsers; no UI or serial dependency."""

import json
import math
import re
import xml.etree.ElementTree as ET


def finite(value):
    if isinstance(value, bool):
        raise ValueError("Boolean is not a measurement")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("Non-finite measurement")
    return result


class LineFramer:
    """Accept CR, LF, or CRLF, including partial/coalesced serial reads."""

    def __init__(self, limit=4096):
        self.buffer = bytearray()
        self.limit = limit
        self.dropping = False

    def feed(self, chunk):
        lines = []
        for byte in chunk:
            if byte in (10, 13):
                if self.buffer and not self.dropping:
                    lines.append(bytes(self.buffer))
                self.buffer.clear()
                self.dropping = False
            elif not self.dropping:
                if len(self.buffer) >= self.limit:
                    self.buffer.clear()
                    self.dropping = True
                else:
                    self.buffer.append(byte)
        return lines


class LicorFramer:
    """Extract complete LI840 XML roots without relying on USB read boundaries."""

    def __init__(self, limit=16384):
        self.buffer = bytearray()
        self.limit = limit

    def feed(self, chunk):
        self.buffer.extend(chunk)
        frames = []
        while True:
            folded = bytes(self.buffer).lower()
            start = folded.find(b"<li840>")
            if start < 0:
                self.buffer = self.buffer[-6:]
                break
            if start:
                del self.buffer[:start]
                folded = bytes(self.buffer).lower()
            end = folded.find(b"</li840>")
            nested = folded.find(b"<li840>", 7)
            if nested >= 0 and (end < 0 or nested < end):
                del self.buffer[:nested]
                continue
            if end < 0:
                if len(self.buffer) > self.limit:
                    self.buffer.clear()
                break
            end += 8
            frame = bytes(self.buffer[:end])
            del self.buffer[:end]
            if len(frame) <= self.limit:
                frames.append(frame)
        return frames


def parse_licor(frame):
    """Read DATA only; configuration echoes must never refresh measurements."""
    if b"<!" in frame or b"<?" in frame:
        raise ValueError("XML declarations/entities are not accepted")
    try:
        root = ET.fromstring(frame)
    except ET.ParseError as exc:
        raise ValueError(f"Malformed LI-COR XML: {exc}") from exc
    if root.tag.upper() != "LI840":
        raise ValueError("Wrong LI-COR root")
    errors = [child for child in root if child.tag.upper() == "ERROR"]
    if errors:
        raise ValueError("LI-COR reported: " + (errors[0].text or "error")[:160])
    data_nodes = [child for child in root if child.tag.upper() == "DATA"]
    if len(data_nodes) > 1:
        raise ValueError("Duplicate LI-COR DATA block")
    data = data_nodes[0] if data_nodes else None
    if data is None:
        return {}
    fields = {"CO2": "co2_ppm", "CELLTEMP": "analyzer_temp_c"}
    result = {}
    for tag, channel in fields.items():
        matches = [child for child in data if child.tag.upper() == tag]
        if len(matches) > 1:
            raise ValueError("Duplicate LI-COR measurement")
        if matches:
            result[channel] = finite(matches[0].text)
    return result


NUMBER = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?"
BASIS_LINE = re.compile(
    rf"^([A-Z])\s+({NUMBER})C\s+({NUMBER})(SCCM|SLPM)"
    rf"(?:\s+({NUMBER})SP)?\s+([A-Za-z][A-Za-z0-9_.-]{{0,23}})(?:\s+(EXH))?$",
    re.IGNORECASE,
)


def parse_alicat(line, unit_id="A"):
    """BASIS labelled frame, not M/MC pressure/volumetric flow column guesses."""
    value = line.decode("ascii", errors="strict").strip() if isinstance(line, bytes) else line.strip()
    match = BASIS_LINE.fullmatch(value)
    if not match or match[1].upper() != unit_id.upper():
        raise ValueError("Expected labelled BASIS frame for " + unit_id)
    finite(match[2])
    flow = finite(match[3]) * (1000.0 if match[4].upper() == "SLPM" else 1.0)
    if match[5] is not None:
        finite(match[5])
    return {"flow_sccm": flow}, match[6], match[7] is not None


def core_message(line):
    def reject_constant(value):
        raise ValueError("Invalid JSON number: " + value)
    obj = json.loads(line, parse_constant=reject_constant)
    if not isinstance(obj, dict) or obj.get("protocol") != "cct-usb" or obj.get("version") != 1:
        raise ValueError("Core2 needs cct-usb protocol version 1 bridge firmware")
    if obj.get("type") not in {"hello", "status", "ack", "error", "event"}:
        raise ValueError("Unknown Core2 message type")
    return obj


ALLOWED_146C = {"status", "sel gas a span 0", "sel gas a span 1", "sel gas off"}


def calibrator_frame(command, instrument_id=51):
    if command not in ALLOWED_146C or type(instrument_id) is not int or not 0 <= instrument_id <= 127:
        raise ValueError("146C command or instrument ID not allowed")
    return bytes([0x80 + instrument_id]) + command.encode("ascii") + b"\r"


def licor_output_config(rate_s=1.0):
    rate = finite(rate_s)
    if rate not in (0.5, 1.0, 2.0, 5.0):
        raise ValueError("Unsupported LI-COR output interval")
    return (f"<LI840><CFG><OUTRATE>{rate:g}</OUTRATE></CFG><RS232>"
            "<STRIP>FALSE</STRIP><ECHO>FALSE</ECHO><CO2>TRUE</CO2>"
            "<H2O>FALSE</H2O><CELLTEMP>TRUE</CELLTEMP></RS232></LI840>\n").encode("ascii")
