"""Local BroadLink/DNA protocol used by TCL Intelligent AC devices."""

from __future__ import annotations

import json
import math
import os
import socket
import struct
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from ipaddress import ip_address, ip_network
from typing import Any

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

IV = bytes.fromhex("562e17996d093d28ddb3ba695a2e6f58")
MAGIC = bytes.fromhex("5aa5aa555aa5aa55")
INNER_MAGIC = bytes.fromhex("a5a55a5a")
DEVICE_TYPE = 0x507C
# The app writes a region-specific type while pairing: United States / Other 0x507c,
# Europe 0x507a. Both answer the same JSON protocol under the 0x507c request header.
SUPPORTED_DEVICE_TYPES = frozenset({DEVICE_TYPE, 0x507A})
COMMAND = 0x006A
BROADLINK_AUTH_COMMAND = 0x0065
BROADLINK_INIT_KEY = bytes.fromhex("097628343fe99e23765c1513accf8b02")
RESPONSE_COMMAND = 0x03EE
DEFAULT_PORT = 80
DEFAULT_TIMEOUT = 3.0
# A request is sent again at this interval until a reply arrives or the request
# times out, so one lost datagram on a 2.4 GHz link does not fail the request.
RESEND_INTERVAL = 1.0
PROBE_TIMEOUT = 1.5
BROADCAST_ADDRESS = "255.255.255.255"
ARP_TABLE = "/proc/net/arp"
ARP_COMPLETE_FLAG = 0x02
DISCOVERY_PAYLOAD = bytes.fromhex(
    "5aa5aa555aa5aa5502000000ea07041f0a001805000000001002000a688d0000ffc40000000006000000000000000000"
)
KNOWN_MAC_PREFIXES = ("24dfa7", "34ea34", "ec0bae", "b4430d", "a043b0")

# What a device that stopped answering control requests still answers.
LINK_MOVED = "moved"  # a broadcast hello is answered from another address
LINK_CONTROL_STUCK = "control_stuck"  # hellos are answered at the configured address
LINK_NO_BROADCAST = "no_broadcast"  # only the hello sent straight to the address is answered
LINK_ARP_ONLY = "arp_only"  # ARP resolves the address to the device, hellos go unanswered
LINK_ADDRESS_TAKEN = "address_taken"  # ARP resolves the address to another device
LINK_UNREACHABLE = "unreachable"  # ARP does not resolve the address
LINK_SILENT = "silent"  # hellos go unanswered and the ARP table is unavailable


class TclAcError(Exception):
    """Raised when local AC communication fails."""


class TclAcConnectionError(TclAcError):
    """Raised when a device does not answer or cannot be reached."""


class TclAcAuthError(TclAcError):
    """Raised when BroadLink/DNA authentication fails."""

    def __init__(self, message: str, error_code: int | None = None) -> None:
        super().__init__(message)
        self.error_code = error_code


class TclAcLockedError(TclAcAuthError):
    """Raised when a device reports the BroadLink/DNA locked flag."""


class TclAcResponseError(TclAcError):
    """Raised when a device answers a local command with a BroadLink/DNA error code."""

    def __init__(self, message: str, error_code: int) -> None:
        super().__init__(message)
        self.error_code = error_code


@dataclass(frozen=True)
class TclAcDevice:
    """Connection details for one TCL AC."""

    host: str
    mac: str
    key: str
    device_id: int = 1
    port: int = DEFAULT_PORT
    timeout: float = DEFAULT_TIMEOUT


@dataclass(frozen=True)
class TclAcDiscovery:
    """Device details returned by BroadLink/DNA discovery."""

    host: str
    mac: str
    devtype: int
    name: str
    is_locked: bool


@dataclass(frozen=True)
class TclAcAuth:
    """Local authentication details returned by a BroadLink/DNA device."""

    key: str
    device_id: int


@dataclass(frozen=True)
class TclAcProbe:
    """What a device that stopped answering control requests still answers."""

    address: str
    """Configured host, resolved to an IPv4 address when possible."""
    direct: bool
    """Whether a discovery hello sent straight to the address was answered."""
    broadcast_host: str | None
    """Address that answered a broadcast discovery hello for this MAC."""
    arp_mac: str | None
    """ARP entry for the address: its MAC, "" if unresolved, None if unknown."""


class TclAcClient:
    """Small synchronous local client for TCL Intelligent AC devices."""

    def __init__(self, device: TclAcDevice) -> None:
        self.device = device
        self._key = _parse_key(device.key)
        self._mac_reversed = _parse_mac(device.mac)[::-1]
        # Requests are resent while unanswered. Serialising them keeps a late
        # repeat of an older write from landing after a newer write.
        self._lock = threading.Lock()

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

        with self._lock:
            # Read the device inside the lock: the coordinator may have moved
            # it to a new host while this request waited.
            device = self.device
            response, _ = _exchange((device.host, device.port), packet, device.timeout)

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
        struct.pack_into("<I", packet, 0x30, self.device.device_id)
        struct.pack_into("<H", packet, 0x34, _checksum(plain_payload))
        packet[0x38:] = encrypted

        packet_for_checksum = bytearray(packet)
        packet_for_checksum[0x20:0x22] = b"\x00\x00"
        struct.pack_into("<H", packet, 0x20, _checksum(packet_for_checksum))
        return bytes(packet)

    def _decode_response(self, packet: bytes) -> dict[str, Any]:
        if len(packet) < 0x38:
            raise TclAcError(f"Response too short: {len(packet)}")
        if struct.unpack_from("<H", packet, 0x26)[0] != RESPONSE_COMMAND:
            command = struct.unpack_from("<H", packet, 0x26)[0]
            raise TclAcError(f"Unexpected response command: 0x{command:04x}")

        # Error replies may be header-only or carry an empty payload, so check the code first.
        error_code = struct.unpack_from("<H", packet, 0x22)[0]
        if error_code != 0:
            signed_code = struct.unpack_from("<h", packet, 0x22)[0]
            raise TclAcResponseError(
                f"Device returned error 0x{error_code:04x} ({signed_code})",
                error_code=error_code,
            )
        if len(packet) < 72:
            raise TclAcError(f"Response too short: {len(packet)}")

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


def discover_tcl_ac_devices(
    timeout: float = 2.0,
    seed_hosts: list[str] | None = None,
    scan_subnet: bool = False,
) -> list[TclAcDiscovery]:
    """Discover TCL/BroadLink DNA AC devices on the LAN."""

    devices: dict[str, TclAcDiscovery] = {}
    targets: list[tuple[str, int]] = [
        ("255.255.255.255", DEFAULT_PORT),
        ("224.0.0.251", DEFAULT_PORT),
        ("224.0.0.251", 16680),
    ]

    seen_targets = {target for target in targets}

    def add_target(host: str, port: int = DEFAULT_PORT) -> None:
        target = (host, port)
        if host and target not in seen_targets:
            seen_targets.add(target)
            targets.append(target)

    for host in seed_hosts or []:
        add_target(host)

    if scan_subnet:
        for host in _candidate_hosts(seed_hosts):
            add_target(host)

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.settimeout(0.2)
        sock.bind(("0.0.0.0", 0))

        for host, port in targets:
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

            discovery = _parse_discovery_response(packet, remote[0])
            if discovery is not None:
                devices[discovery.mac] = discovery

    return sorted(devices.values(), key=lambda device: (device.host, device.mac))


def authenticate_tcl_ac_device(device: TclAcDiscovery, timeout: float = 3.0) -> TclAcAuth:
    """Authenticate to a discovered TCL/BroadLink DNA AC and return the local key."""

    if device.is_locked:
        raise TclAcLockedError(
            f"{device.name} ({device.mac}) reports LOCKED=True and blocks local authentication",
            error_code=0xFFFF,
        )

    payload = bytearray(0x50)
    payload[0x04:0x14] = b"\x31" * 16
    payload[0x1E] = 0x01
    payload[0x2D] = 0x01
    payload[0x30:0x36] = b"Test 1"

    packet = _build_broadlink_packet(
        devtype=device.devtype,
        command=BROADLINK_AUTH_COMMAND,
        mac=device.mac,
        device_id=0,
        key=BROADLINK_INIT_KEY,
        plain_payload=bytes(payload),
    )

    response = _send_udp(device.host, DEFAULT_PORT, packet, timeout)
    if len(response) < 0x48:
        raise TclAcError(f"Authentication response too short: {len(response)}")

    error_code = struct.unpack_from("<H", response, 0x22)[0]
    if error_code != 0:
        if error_code == 0xFFFF:
            raise TclAcLockedError(
                f"Authentication failed with error 0x{error_code:04x}; the device may be locked",
                error_code=error_code,
            )
        raise TclAcAuthError(f"Authentication failed with error 0x{error_code:04x}", error_code=error_code)

    plain = _aes_cbc_decrypt(response[0x38:], BROADLINK_INIT_KEY)
    return TclAcAuth(
        key=plain[0x04:0x14].hex(),
        device_id=struct.unpack_from("<I", plain, 0)[0],
    )


def discover_authenticated_tcl_ac_devices(
    timeout: float = 3.0,
    seed_hosts: list[str] | None = None,
    scan_subnet: bool = True,
) -> list[tuple[TclAcDiscovery, TclAcAuth]]:
    """Discover and authenticate LAN devices that accept TCL local commands."""

    authenticated: list[tuple[TclAcDiscovery, TclAcAuth]] = []
    for discovery in discover_tcl_ac_devices(
        timeout=timeout,
        seed_hosts=seed_hosts,
        scan_subnet=scan_subnet,
    ):
        try:
            auth = authenticate_tcl_ac_device(discovery, timeout=timeout)
            client = TclAcClient(
                TclAcDevice(
                    host=discovery.host,
                    mac=discovery.mac,
                    key=auth.key,
                    device_id=auth.device_id,
                    timeout=timeout,
                )
            )
            client.get_state()
        except Exception:  # noqa: BLE001
            continue
        authenticated.append((discovery, auth))

    return authenticated


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


def probe_device(host: str, mac: str, timeout: float = PROBE_TIMEOUT) -> TclAcProbe:
    """Check what a device that ignores control requests still answers.

    Sends the discovery hello used by local discovery, first straight to the
    host and then by broadcast, and reads the ARP entry for the host. Hellos
    have no side effects on the device.
    """

    target_mac = normalize_mac(mac)
    try:
        address = socket.gethostbyname(host)
    except OSError:
        address = host

    def from_device(reply: bytes, sender: str) -> bool:
        discovery = _parse_discovery_response(reply, sender)
        return discovery is not None and discovery.mac == target_mac

    def hello(destination: str) -> str | None:
        try:
            _, sender = _exchange(
                (destination, DEFAULT_PORT),
                DISCOVERY_PAYLOAD,
                timeout,
                accept=from_device,
                broadcast=destination == BROADCAST_ADDRESS,
            )
        except TclAcConnectionError:
            return None
        return sender

    direct = hello(address) is not None
    broadcast_host = hello(BROADCAST_ADDRESS)
    return TclAcProbe(
        address=address,
        direct=direct,
        broadcast_host=broadcast_host,
        arp_mac=read_arp_mac(address),
    )


def diagnose_probe(probe: TclAcProbe, mac: str) -> str:
    """Name the failure mode a probe of a silent device points to."""

    if probe.direct:
        return LINK_CONTROL_STUCK if probe.broadcast_host else LINK_NO_BROADCAST
    if probe.broadcast_host:
        return LINK_CONTROL_STUCK if probe.broadcast_host == probe.address else LINK_MOVED
    if probe.arp_mac is None:
        return LINK_SILENT
    if not probe.arp_mac:
        return LINK_UNREACHABLE
    return LINK_ARP_ONLY if probe.arp_mac == normalize_mac(mac) else LINK_ADDRESS_TAKEN


def read_arp_mac(address: str, path: str | None = None) -> str | None:
    """Return the MAC this host's ARP table holds for an address.

    Returns "" when the entry is unresolved, and None when the table cannot be
    read (it exists only on Linux) or has no entry for the address.
    """

    try:
        with open(path or ARP_TABLE, encoding="ascii") as table:
            rows = [line.split() for line in table.readlines()[1:]]
    except (OSError, UnicodeDecodeError):
        return None

    found = False
    for row in rows:
        if len(row) < 4 or row[0] != address:
            continue
        found = True
        try:
            if int(row[2], 16) & ARP_COMPLETE_FLAG:
                return normalize_mac(row[3])
        except ValueError:
            continue
    return "" if found else None


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


def _exchange(
    target: tuple[str, int],
    packet: bytes,
    timeout: float,
    accept: Callable[[bytes, str], bool] | None = None,
    broadcast: bool = False,
) -> tuple[bytes, str]:
    """Send a datagram and return the first accepted reply and its sender.

    The same datagram goes out again every RESEND_INTERVAL seconds until a reply
    arrives or the timeout expires. Repeating it is safe: state requests and
    discovery hellos have no side effects, and writes set absolute values.
    """

    host, port = target
    deadline = time.monotonic() + timeout
    next_send = time.monotonic()
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        if broadcast:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        while (now := time.monotonic()) < deadline:
            if now >= next_send:
                try:
                    sock.sendto(packet, target)
                except OSError as exc:
                    raise TclAcConnectionError(f"Cannot send to {host}:{port}: {exc}") from exc
                next_send = now + RESEND_INTERVAL
            sock.settimeout(max(min(next_send, deadline) - now, 0.001))
            try:
                reply, sender = sock.recvfrom(2048)
            except TimeoutError:
                continue
            except OSError as exc:
                raise TclAcConnectionError(f"Cannot receive from {host}:{port}: {exc}") from exc
            if accept is None or accept(reply, sender[0]):
                return reply, sender[0]

    raise TclAcConnectionError(f"Timeout waiting for {host}:{port}")


def _send_udp(host: str, port: int, packet: bytes, timeout: float) -> bytes:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(timeout)
        sock.sendto(packet, (host, port))
        try:
            response, _ = sock.recvfrom(2048)
        except TimeoutError as exc:
            raise TclAcError(f"Timeout waiting for {host}:{port}") from exc
    return response


def _build_broadlink_packet(
    devtype: int,
    command: int,
    mac: str,
    device_id: int,
    key: bytes,
    plain_payload: bytes,
) -> bytes:
    padded_payload = _pad16(plain_payload)
    encrypted = _aes_cbc_encrypt(padded_payload, key)
    packet = bytearray(56 + len(encrypted))

    packet[0:8] = MAGIC
    struct.pack_into("<H", packet, 0x24, devtype)
    struct.pack_into("<H", packet, 0x26, command)
    packet[0x28:0x2A] = os.urandom(2)
    packet[0x2A:0x30] = _parse_mac(mac)[::-1]
    struct.pack_into("<I", packet, 0x30, device_id)
    struct.pack_into("<H", packet, 0x34, _checksum(plain_payload))
    packet[0x38:] = encrypted
    struct.pack_into("<H", packet, 0x20, _checksum(bytes(packet)))
    return bytes(packet)


def _pad16(payload: bytes) -> bytes:
    padding = (16 - len(payload) % 16) % 16
    if padding == 0:
        return payload
    return payload + (b"\x00" * padding)


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


def _parse_discovery_response(packet: bytes, host: str) -> TclAcDiscovery | None:
    if len(packet) < 0x40:
        return None

    devtype = struct.unpack_from("<H", packet, 0x34)[0]
    if devtype not in SUPPORTED_DEVICE_TYPES:
        return None

    mac = normalize_mac(packet[0x3A:0x40][::-1].hex())
    name = packet[0x40:].split(b"\x00")[0].decode(errors="ignore").strip()
    return TclAcDiscovery(
        host=host,
        mac=mac,
        devtype=devtype,
        name=name or f"TCL AC {mac[-8:]}",
        is_locked=bool(packet[0x7F]) if len(packet) > 0x7F else False,
    )


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
