"""Exercise the local protocol without Home Assistant or any real device.

Exchange tests use a loopback socket in place of the AC.
"""

from __future__ import annotations

import importlib
import itertools
from pathlib import Path
import socket
import struct
import sys
import tempfile
import threading
from types import ModuleType
import unittest
from unittest.mock import patch

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



def discovery_reply(devtype, mac="24:df:a7:00:00:01", name=b"OEM_TCLISO_000001"):
    """Frame a BroadLink/DNA discovery reply as the module sends it."""

    packet = bytearray(0x80)
    struct.pack_into("<H", packet, 0x34, devtype)
    packet[0x3A:0x40] = bytes.fromhex(mac.replace(":", ""))[::-1]
    packet[0x40 : 0x40 + len(name)] = name
    return bytes(packet)


class DiscoveryParsingTests(unittest.TestCase):
    def test_united_states_region_device_is_discovered(self):
        found = protocol._parse_discovery_response(discovery_reply(0x507C), "192.0.2.10")
        self.assertEqual(found.devtype, 0x507C)
        self.assertEqual(found.mac, "24:df:a7:00:00:01")
        self.assertEqual(found.name, "OEM_TCLISO_000001")

    def test_europe_region_device_is_discovered(self):
        # A Star-Light ACT-12EAWF paired in the Europe region reports 0x507a and
        # answers the same JSON protocol as 0x507c units.
        found = protocol._parse_discovery_response(discovery_reply(0x507A), "192.0.2.10")
        self.assertIsNotNone(found)
        self.assertEqual(found.devtype, 0x507A)
        self.assertEqual(found.host, "192.0.2.10")

    def test_older_module_is_not_discovered(self):
        # Issue #8: 0x4e2e needs a different, binary protocol.
        self.assertIsNone(
            protocol._parse_discovery_response(discovery_reply(0x4E2E), "192.0.2.10")
        )


class ExchangeTests(unittest.TestCase):
    def setUp(self):
        self.ac_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.addCleanup(self.ac_socket.close)
        self.ac_socket.bind(("127.0.0.1", 0))
        self.ac_socket.settimeout(2)
        self.target = self.ac_socket.getsockname()
        resend = patch.object(protocol, "RESEND_INTERVAL", 0.05)
        resend.start()
        self.addCleanup(resend.stop)

    def run_ac(self, behaviour):
        thread = threading.Thread(target=behaviour)
        thread.start()
        self.addCleanup(thread.join)
        return thread

    def drain(self):
        self.ac_socket.settimeout(0.3)
        packets = []
        try:
            while True:
                packets.append(self.ac_socket.recvfrom(2048)[0])
        except TimeoutError:
            return packets

    def test_unanswered_request_is_repeated_until_a_reply_arrives(self):
        received = []

        def ac():
            for _ in range(2):
                packet, sender = self.ac_socket.recvfrom(2048)
                received.append(packet)
            self.ac_socket.sendto(b"reply", sender)

        self.run_ac(ac)
        self.assertEqual(protocol._exchange(self.target, b"request", 2.0), (b"reply", "127.0.0.1"))
        self.assertEqual(received, [b"request", b"request"])

    def test_silent_device_times_out_as_connection_error(self):
        with self.assertRaisesRegex(protocol.TclAcConnectionError, f"Timeout waiting for 127.0.0.1:{self.target[1]}"):
            protocol._exchange(self.target, b"request", 0.2)
        self.assertGreaterEqual(len(self.drain()), 2)

    def test_replies_the_caller_rejects_are_skipped(self):
        def ac():
            _, sender = self.ac_socket.recvfrom(2048)
            self.ac_socket.sendto(b"other device", sender)
            self.ac_socket.sendto(b"this device", sender)

        self.run_ac(ac)
        reply, _ = protocol._exchange(self.target, b"hello", 2.0, accept=lambda packet, sender: packet == b"this device")
        self.assertEqual(reply, b"this device")

    def test_client_timeout_stays_a_tcl_error(self):
        ac = protocol.TclAcClient(
            protocol.TclAcDevice(
                host="127.0.0.1", mac="02:00:00:00:00:01", key=SYNTHETIC_KEY, port=self.target[1], timeout=0.2
            )
        )
        with self.assertRaises(protocol.TclAcConnectionError) as caught:
            ac.get_state()
        self.assertIsInstance(caught.exception, protocol.TclAcError)

    def test_repeats_of_one_write_never_follow_a_later_write(self):
        ac = protocol.TclAcClient(
            protocol.TclAcDevice(
                host="127.0.0.1", mac="02:00:00:00:00:01", key=SYNTHETIC_KEY, port=self.target[1], timeout=0.3
            )
        )

        def write(value):
            with self.assertRaises(protocol.TclAcConnectionError):
                ac.set_param("temp", value)

        writers = [threading.Thread(target=write, args=(value,)) for value in (220, 230)]
        for writer in writers:
            writer.start()
        for writer in writers:
            writer.join()
        packets = self.drain()
        self.assertGreaterEqual(len(packets), 4)
        # Each write repeats one datagram; the AC must see one run per write.
        self.assertEqual(len([key for key, _ in itertools.groupby(packets)]), 2)


class ProbeTests(unittest.TestCase):
    MAC = "02:00:00:00:00:01"

    def diagnose(self, direct=False, broadcast_host=None, arp_mac=None):
        probe = protocol.TclAcProbe("192.0.2.10", direct, broadcast_host, arp_mac)
        return protocol.diagnose_probe(probe, self.MAC)

    def test_diagnoses(self):
        cases = [
            ({"direct": True, "broadcast_host": "192.0.2.10"}, protocol.LINK_CONTROL_STUCK),
            ({"broadcast_host": "192.0.2.10"}, protocol.LINK_CONTROL_STUCK),
            ({"direct": True}, protocol.LINK_NO_BROADCAST),
            ({"broadcast_host": "192.0.2.20"}, protocol.LINK_MOVED),
            ({"arp_mac": self.MAC}, protocol.LINK_ARP_ONLY),
            ({"arp_mac": "02:00:00:00:00:99"}, protocol.LINK_ADDRESS_TAKEN),
            ({"arp_mac": ""}, protocol.LINK_UNREACHABLE),
            ({}, protocol.LINK_SILENT),
        ]
        for answers, expected in cases:
            with self.subTest(answers=answers):
                self.assertEqual(self.diagnose(**answers), expected)

    def test_probe_sends_hellos_directly_then_by_broadcast_and_matches_the_mac(self):
        calls = []

        def exchange(target, packet, timeout, accept=None, broadcast=False):
            calls.append((target, broadcast))
            self.assertEqual(packet, protocol.DISCOVERY_PAYLOAD)
            if target[0] == "192.0.2.10":
                raise protocol.TclAcConnectionError("Timeout waiting for 192.0.2.10:80")
            for reply, sender in (
                (discovery_reply(protocol.DEVICE_TYPE, mac="02:00:00:00:00:99"), "192.0.2.30"),
                (discovery_reply(protocol.DEVICE_TYPE, mac=self.MAC), "192.0.2.20"),
            ):
                if accept(reply, sender):
                    return reply, sender
            raise protocol.TclAcConnectionError("Timeout waiting for 255.255.255.255:80")

        with patch.object(protocol, "_exchange", exchange), patch.object(protocol, "read_arp_mac", return_value=""):
            probe = protocol.probe_device("192.0.2.10", self.MAC)

        self.assertEqual(probe, protocol.TclAcProbe("192.0.2.10", False, "192.0.2.20", ""))
        self.assertEqual(calls, [(("192.0.2.10", 80), False), (("255.255.255.255", 80), True)])

    def test_probe_recognises_europe_region_units(self):
        def exchange(target, packet, timeout, accept=None, broadcast=False):
            reply = discovery_reply(0x507A, mac=self.MAC)
            if broadcast and accept(reply, "192.0.2.20"):
                return reply, "192.0.2.20"
            raise protocol.TclAcConnectionError(f"Timeout waiting for {target[0]}:80")

        with patch.object(protocol, "_exchange", exchange), patch.object(protocol, "read_arp_mac", return_value=""):
            probe = protocol.probe_device("192.0.2.10", self.MAC)

        self.assertEqual(protocol.diagnose_probe(probe, self.MAC), protocol.LINK_MOVED)


class ArpTableTests(unittest.TestCase):
    TABLE = (
        "IP address       HW type     Flags       HW address            Mask     Device\n"
        "192.0.2.10       0x1         0x2         02:00:00:00:00:01     *        enp0s3\n"
        "192.0.2.11       0x1         0x0         00:00:00:00:00:00     *        enp0s3\n"
    )

    def read(self, address):
        with tempfile.NamedTemporaryFile("w", suffix=".arp", delete=False) as table:
            table.write(self.TABLE)
        self.addCleanup(Path(table.name).unlink)
        return protocol.read_arp_mac(address, table.name)

    def test_resolved_entry_returns_its_mac(self):
        self.assertEqual(self.read("192.0.2.10"), "02:00:00:00:00:01")

    def test_unresolved_entry_is_empty(self):
        self.assertEqual(self.read("192.0.2.11"), "")

    def test_missing_entry_or_table_is_unknown(self):
        self.assertIsNone(self.read("192.0.2.12"))
        self.assertIsNone(protocol.read_arp_mac("192.0.2.10", "/nonexistent/arp"))


if __name__ == "__main__":
    unittest.main()
