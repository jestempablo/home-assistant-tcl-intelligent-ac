"""Exercise host updates from discovery without starting Home Assistant."""

from __future__ import annotations

import importlib
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.service_info.dhcp import DhcpServiceInfo

PACKAGE_NAME = "tcl_config_flow_test"
package = ModuleType(PACKAGE_NAME)
package.__path__ = [
    str(Path(__file__).resolve().parents[1] / "custom_components" / "tcl_intelligent_ac")
]
sys.modules[PACKAGE_NAME] = package
config_flow = importlib.import_module(f"{PACKAGE_NAME}.config_flow")
protocol = importlib.import_module(f"{PACKAGE_NAME}.protocol")

MAC = "02:00:00:00:00:01"
OTHER_MAC = "02:00:00:00:00:02"
NEW_MAC = "02:00:00:00:00:03"
KEY = "00112233445566778899aabbccddeeff"


class FakeEntries:
    """Config entry store that reports changes like Home Assistant does."""

    def __init__(self, **entries):
        self.entries = [SimpleNamespace(entry_id=entry_id, data=data) for entry_id, data in entries.items()]
        self.reloaded = []

    def async_entries(self, domain):
        assert domain == config_flow.DOMAIN
        return self.entries

    def async_update_entry(self, entry, *, data):
        if dict(entry.data) == data:
            return False
        entry.data = data
        return True

    def async_schedule_reload(self, entry_id):
        self.reloaded.append(entry_id)


def two_devices():
    return {
        "devices": [
            {"name": "Master", "host": "192.0.2.10", "mac": MAC, "key": KEY},
            {"name": "Bedroom", "host": "192.0.2.11", "mac": OTHER_MAC, "key": KEY},
        ]
    }


class HostUpdateTests(unittest.TestCase):
    def update(self, entries, mac, host):
        return config_flow._update_configured_host(SimpleNamespace(config_entries=entries), mac, host)

    def test_new_host_is_stored_and_the_entry_reloaded(self):
        entries = FakeEntries(both=two_devices())
        self.assertTrue(self.update(entries, "020000000001", "192.0.2.20"))
        self.assertEqual([device["host"] for device in entries.entries[0].data["devices"]], ["192.0.2.20", "192.0.2.11"])
        self.assertEqual(entries.reloaded, ["both"])

    def test_unchanged_host_does_not_reload(self):
        entries = FakeEntries(both=two_devices())
        self.assertTrue(self.update(entries, MAC, "192.0.2.10"))
        self.assertEqual(entries.reloaded, [])

    def test_single_device_entry_is_updated(self):
        entries = FakeEntries(single={"name": "Master", "host": "192.0.2.10", "mac": MAC, "key": KEY})
        self.assertTrue(self.update(entries, MAC, "192.0.2.20"))
        self.assertEqual(entries.entries[0].data["host"], "192.0.2.20")
        self.assertEqual(entries.reloaded, ["single"])

    def test_unknown_device_is_left_alone(self):
        entries = FakeEntries(both=two_devices())
        self.assertFalse(self.update(entries, "02:00:00:00:00:03", "192.0.2.20"))
        self.assertEqual(entries.entries[0].data, two_devices())
        self.assertEqual(entries.reloaded, [])


class DhcpTests(unittest.IsolatedAsyncioTestCase):
    def flow(self, entries, executor):
        flow = config_flow.ConfigFlow()
        flow.hass = SimpleNamespace(config_entries=entries, async_add_executor_job=executor)
        return flow

    async def test_configured_device_gets_new_host_without_network_requests(self):
        entries = FakeEntries(both=two_devices())
        executor = AsyncMock()
        result = await self.flow(entries, executor).async_step_dhcp(
            DhcpServiceInfo(ip="192.0.2.20", hostname="oem_tcliso_000001", macaddress="020000000001")
        )
        self.assertEqual(result["type"], FlowResultType.ABORT)
        self.assertEqual(result["reason"], "already_configured")
        executor.assert_not_called()
        self.assertEqual(entries.entries[0].data["devices"][0]["host"], "192.0.2.20")
        self.assertEqual(entries.reloaded, ["both"])

    async def test_new_device_is_still_validated_on_the_lan(self):
        entries = FakeEntries(both=two_devices())
        executor = AsyncMock(return_value=config_flow.LocalDiscoveryResult(devices=[], locked_devices=[]))
        result = await self.flow(entries, executor).async_step_dhcp(
            DhcpServiceInfo(ip="192.0.2.30", hostname="oem_tcliso_000003", macaddress="020000000003")
        )
        self.assertEqual(result["reason"], "not_supported")
        executor.assert_awaited_once_with(
            config_flow._accountless_device_configs, ["192.0.2.30"], False, "192.0.2.30"
        )
        self.assertEqual(entries.reloaded, [])

    async def test_new_unit_is_offered_when_a_configured_unit_sorts_first(self):
        # "192.0.2.10" sorts before "192.0.2.9", so the configured unit comes
        # first in discovery and used to mask the unit DHCP announced.
        async def executor(func, *args):
            return func(*args)

        entries = FakeEntries(both=two_devices())
        flow = self.flow(entries, executor)
        flow.context = {}
        flow.async_set_unique_id = AsyncMock()
        flow._abort_if_unique_id_configured = MagicMock()
        found = [
            protocol.TclAcDiscovery("192.0.2.10", MAC, 0x507C, "Master", False),
            protocol.TclAcDiscovery("192.0.2.9", NEW_MAC, 0x507A, "Guest room", False),
        ]
        with discovering(found) as authenticate:
            result = await flow.async_step_dhcp(
                DhcpServiceInfo(ip="192.0.2.9", hostname="oem_tcliso_000003", macaddress="020000000003")
            )
        self.assertEqual(result["type"], FlowResultType.FORM)
        self.assertEqual(result["description_placeholders"]["host"], "192.0.2.9")
        self.assertEqual(result["description_placeholders"]["mac"], NEW_MAC)
        authenticate.assert_called_once_with(found[1])
        self.assertEqual(entries.reloaded, [])


def discovering(found):
    """Patch LAN discovery to return found units, each answering authentication."""

    auth = protocol.TclAcAuth(key=KEY, device_id=1)
    patches = [
        patch.object(config_flow, "discover_tcl_ac_devices", return_value=found),
        patch.object(config_flow, "TclAcClient"),
    ]
    authenticate = patch.object(config_flow, "authenticate_tcl_ac_device", return_value=auth)

    class Context:
        def __enter__(self):
            for active in patches:
                active.start()
            return authenticate.start()

        def __exit__(self, *exc):
            authenticate.stop()
            for active in reversed(patches):
                active.stop()

    return Context()


class AccountlessDiscoveryTests(unittest.TestCase):
    FOUND = [
        protocol.TclAcDiscovery("192.0.2.20", MAC, 0x507C, "Master", False),
        protocol.TclAcDiscovery("192.0.2.30", NEW_MAC, 0x507A, "Guest room", False),
    ]

    def test_only_the_requested_host_is_authenticated(self):
        with discovering(self.FOUND) as authenticate:
            result = config_flow._accountless_device_configs(["192.0.2.30"], False, "192.0.2.30")
        authenticate.assert_called_once_with(self.FOUND[1])
        self.assertEqual([device["host"] for device in result.devices], ["192.0.2.30"])

    def test_user_discovery_still_authenticates_every_unit(self):
        with discovering(self.FOUND) as authenticate:
            result = config_flow._accountless_device_configs(["192.0.2.30"], True)
        self.assertEqual(authenticate.call_count, 2)
        self.assertEqual([device["host"] for device in result.devices], ["192.0.2.20", "192.0.2.30"])


if __name__ == "__main__":
    unittest.main()
