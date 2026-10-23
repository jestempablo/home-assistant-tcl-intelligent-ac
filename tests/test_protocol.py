"""Decode local device replies without starting Home Assistant or opening sockets."""

from __future__ import annotations

import importlib
from pathlib import Path
import struct
import sys
from types import ModuleType
import unittest

# Supply a package namespace only: protocol.py needs no HA modules.
PACKAGE_NAME = "tcl_protocol_test"
package = ModuleType(PACKAGE_NAME)
package.__path__ = [
    str(Path(__file__).resolve().parents[1] / "custom_components" / "tcl_intelligent_ac")
]
sys.modules[PACKAGE_NAME] = package
protocol = importlib.import_module(f"{PACKAGE_NAME}.protocol")

SYNTHETIC_KEY = "00112233445566778899aabbccddeeff"


def client():
    return protocol.TclAcClient(
        protocol.TclAcDevice(host="192.0.2.10", mac="02:00:00:00:00:01", key=SYNTHETIC_KEY)
    )


def reply(ac, plain, error_code=0):
    """Frame plain bytes like a device reply to the 0x006a state command."""

    packet = bytearray(ac._build_packet(plain))
    struct.pack_into("<H", packet, 0x26, protocol.RESPONSE_COMMAND)
    struct.pack_into("<H", packet, 0x22, error_code)
    return bytes(packet)


class ResponseDecodingTests(unittest.TestCase):
    def test_state_reply_returns_json_body(self):
        ac = client()
        plain = ac._encode_inner_payload("get", {"pwr": 1, "temp": 230})
        self.assertEqual(ac._decode_response(reply(ac, plain)), {"pwr": 1, "temp": 230})

    def test_rejected_command_reports_device_error_code(self):
        # Issue #8: a 72-byte reply with error 0xfffb and an empty payload used to
        # surface as "Invalid inner payload length: 0".
        ac = client()
        packet = reply(ac, bytes(16), error_code=0xFFFB)
        self.assertEqual(len(packet), 72)
        with self.assertRaises(protocol.TclAcResponseError) as caught:
            ac._decode_response(packet)
        self.assertEqual(caught.exception.error_code, 0xFFFB)
        self.assertEqual(str(caught.exception), "Device returned error 0xfffb (-5)")

    def test_header_only_error_reply_reports_device_error_code(self):
        ac = client()
        packet = reply(ac, bytes(16), error_code=0xFFFF)[:0x38]
        with self.assertRaises(protocol.TclAcResponseError) as caught:
            ac._decode_response(packet)
        self.assertEqual(caught.exception.error_code, 0xFFFF)
        self.assertIsInstance(caught.exception, protocol.TclAcError)

    def test_header_only_success_reply_is_too_short(self):
        ac = client()
        packet = reply(ac, bytes(16))[:0x38]
        with self.assertRaisesRegex(protocol.TclAcError, "Response too short: 56") as caught:
            ac._decode_response(packet)
        self.assertNotIsInstance(caught.exception, protocol.TclAcResponseError)


if __name__ == "__main__":
    unittest.main()
