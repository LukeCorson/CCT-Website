import unittest

from cct.drivers.s3_usb import (ALLOWED_VALVES, ReplyParser, S3UsbLink,
                                command_frame, mask_to_valves, valves_to_mask)


class FakeSerial:
    def __init__(self, port, baud):
        self.port = port
        self.baud = baud
        self.buffer = bytearray()
        self.counter = 0
        self.closed = False

    @property
    def in_waiting(self):
        return len(self.buffer)

    def write(self, data):
        self.assert_frame(data)
        state = data[1]
        self.counter = (self.counter + 1) & 0xFF
        self.buffer.extend((0x5A, state, self.counter, 0x5A ^ state ^ self.counter))
        return len(data)

    @staticmethod
    def assert_frame(data):
        if len(data) != 3 or data[0] != 0xA5 or data[2] != (0xA5 ^ data[1]):
            raise OSError("bad command frame")

    def flush(self):
        pass

    def read(self, size):
        data = bytes(self.buffer[:size])
        del self.buffer[:size]
        return data

    def close(self):
        self.closed = True


class S3UsbProtocolTests(unittest.TestCase):
    def test_supplied_valve_numbering_maps_to_firmware_bits(self):
        valves = {name: False for name in ALLOWED_VALVES}
        valves["valve_1"] = True
        valves["valve_6"] = True
        self.assertEqual(valves_to_mask(valves), 0b100001)
        self.assertEqual(mask_to_valves(0b100001), valves)
        self.assertEqual(command_frame(0b100001), bytes((0xA5, 0x21, 0x84)))

    def test_reply_checksum_and_noise_handling(self):
        parser = ReplyParser()
        self.assertEqual(parser.feed(b"noise\x5a\x03\x07\x00"), [])
        self.assertEqual(parser.feed(bytes((0x5A, 3, 8, 0x5A ^ 3 ^ 8))),
                         [{"state": 3, "counter": 8}])

    def test_link_confirms_named_valves_and_turns_all_off_on_close(self):
        created = []

        def factory(port, baud):
            serial = FakeSerial(port, baud)
            created.append(serial)
            return serial

        link = S3UsbLink("COM9", serial_factory=factory)
        link.connect()
        valves = {name: name in {"valve_2", "valve_5"} for name in ALLOWED_VALVES}
        snapshot = link.set_valves(valves)
        self.assertEqual(snapshot["applied_valves"], valves)
        self.assertFalse(snapshot["heating_available"])
        link.close()
        self.assertEqual(created[0].buffer, b"")
        self.assertTrue(created[0].closed)


if __name__ == "__main__":
    unittest.main()
