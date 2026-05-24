"""Local BroadLink/DNA protocol used by TCL Intelligent AC devices."""

from __future__ import annotations

import json
import math
import os
import socket
import struct
import time
from dataclasses import dataclass
from ipaddress import ip_address, ip_network
from typing import Any

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

IV = bytes.fromhex("562e17996d093d28ddb3ba695a2e6f58")
MAGIC = bytes.fromhex("5aa5aa555aa5aa55")
INNER_MAGIC = bytes.fromhex("a5a55a5a")
DEVICE_TYPE = 0x507C
COMMAND = 0x006A
RESPONSE_COMMAND = 0x03EE
DEFAULT_PORT = 80
DEFAULT_TIMEOUT = 3.0
DISCOVERY_PAYLOAD = bytes.fromhex(
    "5aa5aa555aa5aa5502000000ea07041f0a001805000000001002000a688d0000ffc40000000006000000000000000000"
)
KNOWN_MAC_PREFIXES = ("24dfa7", "34ea34", "ec0bae", "b4430d", "a043b0")


class TclAcError(Exception):
    """Raised when local AC communication fails."""


@dataclass(frozen=True)
class TclAcDevice:
    """Connection details for one TCL AC."""

    host: str
    mac: str
    key: str
    port: int = DEFAULT_PORT
    timeout: float = DEFAULT_TIMEOUT


class TclAcClient:
    """Small synchronous local client for TCL Intelligent AC devices."""

    def __init__(self, device: TclAcDevice) -> None:
        self.device = device
        self._key = _parse_key(device.key)
        self._mac_reversed = _parse_mac(device.mac)[::-1]

    def get_state(self) -> dict[str, Any]:
        """Read the current raw state from the device."""

        return self._request("get", {})

    def set_param(self, param: str, value: Any) -> dict[str, Any]:
        """Set a single raw TCL parameter and return the resulting state."""

        return self._request("set", {param: value})

    def set_params(self, params: dict[str, Any]) -> dict[str, Any]:
        """Set multiple raw TCL parameters and return the resulting state."""

        return self._request("set", params)

    def _request(self, action: str, body: dict[str, Any]) -> dict[str, Any]:
        plain = self._encode_inner_payload(action, body)
        packet = self._build_packet(plain)

        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.settimeout(self.device.timeout)
            sock.sendto(packet, (self.device.host, self.device.port))
            try:
                response, _ = sock.recvfrom(2048)
            except TimeoutError as exc:
                raise TclAcError(f"Timeout waiting for {self.device.host}:{self.device.port}") from exc

        return self._decode_response(response)

    def _encode_inner_payload(self, action: str, body: dict[str, Any]) -> bytes:
        body_bytes = json.dumps(body, separators=(",", ":")).encode()
        meaningful_length = 12 + len(body_bytes)
        plain_length = math.ceil((14 + len(body_bytes)) / 16) * 16
        plain = bytearray(plain_length)

        struct.pack_into("<H", plain, 0, meaningful_length)
        plain[2:6] = INNER_MAGIC
        plain[8] = 1 if action == "get" else 2
        plain[9] = 0x0B
        struct.pack_into("<I", plain, 10, len(body_bytes))
        plain[14 : 14 + len(body_bytes)] = body_bytes

        inner_checksum = _checksum(bytes(plain[2:6]) + bytes(plain[8:]))
        struct.pack_into("<H", plain, 6, inner_checksum)
        return bytes(plain)

    def _build_packet(self, plain_payload: bytes) -> bytes:
        encrypted = _aes_cbc_encrypt(plain_payload, self._key)
        packet = bytearray(56 + len(encrypted))

        packet[0:8] = MAGIC
        struct.pack_into("<H", packet, 0x24, DEVICE_TYPE)
        struct.pack_into("<H", packet, 0x26, COMMAND)
        packet[0x28:0x2A] = os.urandom(2)
        packet[0x2A:0x30] = self._mac_reversed
        struct.pack_into("<I", packet, 0x30, 1)
        struct.pack_into("<H", packet, 0x34, _checksum(plain_payload))
        packet[0x38:] = encrypted

        packet_for_checksum = bytearray(packet)
        packet_for_checksum[0x20:0x22] = b"\x00\x00"
        struct.pack_into("<H", packet, 0x20, _checksum(packet_for_checksum))
        return bytes(packet)

    def _decode_response(self, packet: bytes) -> dict[str, Any]:
        if len(packet) < 72:
            raise TclAcError(f"Response too short: {len(packet)}")
        if struct.unpack_from("<H", packet, 0x26)[0] != RESPONSE_COMMAND:
            command = struct.unpack_from("<H", packet, 0x26)[0]
            raise TclAcError(f"Unexpected response command: 0x{command:04x}")

        plain = _aes_cbc_decrypt(packet[0x38:], self._key)
        expected_checksum = struct.unpack_from("<H", packet, 0x34)[0]
        actual_checksum = _checksum(plain)
        if expected_checksum != actual_checksum:
            raise TclAcError(
                f"Payload checksum mismatch: got 0x{actual_checksum:04x}, expected 0x{expected_checksum:04x}"
            )

        meaningful_length = struct.unpack_from("<H", plain, 0)[0]
        body_length = meaningful_length - 12
        if body_length < 0 or 14 + body_length > len(plain):
            raise TclAcError(f"Invalid inner payload length: {meaningful_length}")

        return json.loads(plain[14 : 14 + body_length].decode())


def normalize_mac(mac: str) -> str:
    """Normalize a MAC address to lower-case colon notation."""

    compact = _compact_mac(mac)
    return ":".join(compact[index : index + 2] for index in range(0, 12, 2))


def discover_device_hosts(timeout: float = 2.0) -> dict[str, str]:
    """Discover BroadLink/DNA devices on the LAN and return MAC-to-host mapping."""

    devices: dict[str, str] = {}
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.settimeout(0.2)
        sock.bind(("0.0.0.0", 0))

        for host, port in (("255.255.255.255", 80), ("224.0.0.251", 80), ("224.0.0.251", 16680)):
            try:
                sock.sendto(DISCOVERY_PAYLOAD, (host, port))
            except OSError:
                continue

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                packet, remote = sock.recvfrom(2048)
            except TimeoutError:
                continue
            except OSError:
                break
            for mac in _extract_macs(packet):
                devices[mac] = remote[0]

    return devices


def find_device_host(
    mac: str,
    key: str,
    seed_hosts: list[str] | None = None,
    timeout: float = 3.0,
) -> str | None:
    """Find a device host by sending an authenticated local state request."""

    target_mac = normalize_mac(mac)
    discovered = discover_device_hosts(timeout=1.0)
    if discovered.get(target_mac):
        return discovered[target_mac]

    candidates = _candidate_hosts(seed_hosts)
    if not candidates:
        return None

    client = TclAcClient(TclAcDevice(host="0.0.0.0", mac=target_mac, key=key, timeout=timeout))
    packet = client._build_packet(client._encode_inner_payload("get", {}))

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(0.2)
        for host in candidates:
            try:
                sock.sendto(packet, (host, DEFAULT_PORT))
            except OSError:
                continue

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                response, remote = sock.recvfrom(2048)
            except TimeoutError:
                continue
            except OSError:
                break
            try:
                client._decode_response(response)
            except Exception:  # noqa: BLE001
                continue
            return remote[0]

    return None


def _checksum(payload: bytes) -> int:
    value = 0xBEAF
    for byte in payload:
        value = (value + byte) & 0xFFFF
    return value


def _parse_mac(mac: str) -> bytes:
    return bytes.fromhex(_compact_mac(mac))


def _parse_key(key: str) -> bytes:
    compact = key.strip().lower()
    if len(compact) != 32:
        raise ValueError("Device key must be 16 bytes encoded as 32 hex characters")
    return bytes.fromhex(compact)


def _aes_cbc_encrypt(payload: bytes, key: bytes) -> bytes:
    encryptor = Cipher(algorithms.AES(key), modes.CBC(IV)).encryptor()
    return encryptor.update(payload) + encryptor.finalize()


def _aes_cbc_decrypt(payload: bytes, key: bytes) -> bytes:
    decryptor = Cipher(algorithms.AES(key), modes.CBC(IV)).decryptor()
    return decryptor.update(payload) + decryptor.finalize()


def _compact_mac(mac: str) -> str:
    compact = mac.replace(":", "").replace("-", "").lower()
    if len(compact) != 12:
        raise ValueError(f"Invalid MAC address: {mac}")
    return compact


def _extract_macs(packet: bytes) -> list[str]:
    macs = []
    for offset in range(0, max(0, len(packet) - 5)):
        compact = packet[offset : offset + 6][::-1].hex()
        if compact.startswith(KNOWN_MAC_PREFIXES):
            macs.append(normalize_mac(compact))
    return macs


def _candidate_hosts(seed_hosts: list[str] | None = None) -> list[str]:
    candidates: list[str] = []
    seen: set[str] = set()

    def add(host: str) -> None:
        if host and host not in seen:
            seen.add(host)
            candidates.append(host)

    for host in seed_hosts or []:
        add(host)

    for local_ip in _local_ipv4_addresses():
        try:
            network = ip_network(f"{local_ip}/24", strict=False)
        except ValueError:
            continue
        for host in network.hosts():
            add(str(host))

    return candidates


def _local_ipv4_addresses() -> set[str]:
    addresses: set[str] = set()

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            addresses.add(sock.getsockname()[0])
    except OSError:
        pass

    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            addresses.add(info[4][0])
    except OSError:
        pass

    return {
        address
        for address in addresses
        if not ip_address(address).is_loopback and not ip_address(address).is_link_local
    }
