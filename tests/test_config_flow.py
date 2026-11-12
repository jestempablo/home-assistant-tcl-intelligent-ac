"""Exercise host updates from discovery without starting Home Assistant."""

from __future__ import annotations

import importlib
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.service_info.dhcp import DhcpServiceInfo

PACKAGE_NAME = "tcl_config_flow_test"
package = ModuleType(PACKAGE_NAME)
package.__path__ = [
    str(Path(__file__).resolve().parents[1] / "custom_components" / "tcl_intelligent_ac")
]
sys.modules[PACKAGE_NAME] = package
config_flow = importlib.import_module(f"{PACKAGE_NAME}.config_flow")

MAC = "02:00:00:00:00:01"
OTHER_MAC = "02:00:00:00:00:02"
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
        executor.assert_awaited_once_with(config_flow._accountless_device_configs, ["192.0.2.30"], False)
        self.assertEqual(entries.reloaded, [])


if __name__ == "__main__":
    unittest.main()
