"""Exercise how the coordinator handles an AC that stops answering.

Uses the real coordinator class with a fake executor, clock and device boundary.
"""

from __future__ import annotations

import asyncio
import importlib
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from homeassistant.helpers.update_coordinator import UpdateFailed

PACKAGE_NAME = "tcl_coordinator_test"
package = ModuleType(PACKAGE_NAME)
package.__path__ = [
    str(Path(__file__).resolve().parents[1] / "custom_components" / "tcl_intelligent_ac")
]
sys.modules[PACKAGE_NAME] = package
coordinator = importlib.import_module(f"{PACKAGE_NAME}.coordinator")
protocol = importlib.import_module(f"{PACKAGE_NAME}.protocol")

MAC = "02:00:00:00:00:01"
OTHER_MAC = "02:00:00:00:00:02"
KEY = "00112233445566778899aabbccddeeff"
TIMEOUT = protocol.TclAcConnectionError("Timeout waiting for 192.0.2.10:80")
ERROR = protocol.TclAcResponseError("Device returned error 0xfffb (-5)", 0xFFFB)


def probe(direct=False, broadcast_host=None, arp_mac=None):
    return protocol.TclAcProbe("192.0.2.10", direct, broadcast_host, arp_mac)


class FakeEntry:
    def __init__(self, data):
        self.data = data

    def async_on_unload(self, func):
        pass


class FailureTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.now = 1000.0
        clock = patch.object(coordinator, "monotonic", lambda: self.now)
        clock.start()
        self.addCleanup(clock.stop)
        self.probe_gate = None

    def make(self, results, probes, entry=None):
        """Build a coordinator whose polls return or raise results in order."""

        async def executor(func, *args):
            result = func(*args)
            if func is self.probe_device and self.probe_gate is not None:
                await self.probe_gate.wait()  # a probe that is still running
            return result

        remaining = iter(results)
        self.polled_hosts = []

        def get_state():
            self.polled_hosts.append(self.client.device.host)
            result = next(remaining)
            if isinstance(result, Exception):
                raise result
            return result

        self.hass = SimpleNamespace(
            async_add_executor_job=executor,
            config_entries=SimpleNamespace(async_update_entry=MagicMock(return_value=True)),
        )
        self.client = SimpleNamespace(
            device=protocol.TclAcDevice(host="192.0.2.10", mac=MAC, key=KEY),
            get_state=get_state,
            set_param=MagicMock(return_value={"pwr": 1}),
        )
        self.probe_device = MagicMock(side_effect=probes)
        prober = patch.object(coordinator, "probe_device", self.probe_device)
        prober.start()
        self.addCleanup(prober.stop)
        return coordinator.TclAcCoordinator(self.hass, self.client, "Master", entry)

    async def poll(self, ac, minutes=0.5):
        try:
            return await ac._async_update_data()
        except UpdateFailed:
            return None
        finally:
            self.now += minutes * 60

    async def test_probes_after_three_failed_polls_and_reports_once(self):
        ac = self.make([TIMEOUT] * 5, [probe(direct=True, broadcast_host="192.0.2.10")])
        with self.assertLogs(coordinator._LOGGER, "WARNING") as logs:
            for _ in range(5):
                self.assertIsNone(await self.poll(ac))
        self.probe_device.assert_called_once_with("192.0.2.10", MAC)
        self.assertEqual(len(logs.output), 1)
        self.assertIn(
            "Master at 192.0.2.10 has not returned its state for 1 min 0 s "
            "(last error: Timeout waiting for 192.0.2.10:80): it still answers discovery",
            logs.output[0],
        )

    async def test_probes_again_after_interval_and_reports_only_changes(self):
        ac = self.make([TIMEOUT] * 5, [probe(arp_mac=""), probe(arp_mac=""), probe(arp_mac=MAC)])
        with self.assertLogs(coordinator._LOGGER, "WARNING") as logs:
            for minutes in (0.5, 0.5, 10, 10, 1):
                await self.poll(ac, minutes)
        self.assertEqual(self.probe_device.call_count, 3)
        self.assertEqual(len(logs.output), 2)
        self.assertIn("answers neither ARP nor discovery", logs.output[0])
        self.assertIn("answers ARP but not discovery", logs.output[1])

    async def test_reports_recovery_after_a_reported_failure(self):
        ac = self.make([TIMEOUT, TIMEOUT, TIMEOUT, {"pwr": 1}, TIMEOUT], [probe(arp_mac="")])
        with self.assertLogs(coordinator._LOGGER, "WARNING") as logs:
            for _ in range(3):
                await self.poll(ac)
            self.assertEqual(await self.poll(ac), {"pwr": 1})
            await self.poll(ac)
        self.assertEqual(len(logs.output), 2)
        self.assertIn("Master at 192.0.2.10 is responding again after 1 min 30 s", logs.output[1])
        self.assertEqual(self.probe_device.call_count, 1)

    async def test_flapping_link_is_probed_at_most_once_per_interval(self):
        answer = {"pwr": 1}
        ac = self.make([TIMEOUT, TIMEOUT, TIMEOUT, answer] * 2 + [TIMEOUT] * 3, [probe(arp_mac="")] * 3)
        with self.assertLogs(coordinator._LOGGER, "WARNING") as logs:
            for _ in range(11):
                await self.poll(ac)
        self.assertEqual(self.probe_device.call_count, 1)
        self.assertEqual(len(logs.output), 2)

    async def test_error_replies_count_toward_the_probe(self):
        # Whatever answers at the old address may be another device, so the
        # probe still has to look for this one.
        ac = self.make([ERROR] * 3, [probe(direct=True, broadcast_host="192.0.2.10")])
        with self.assertLogs(coordinator._LOGGER, "WARNING") as logs:
            for _ in range(3):
                self.assertIsNone(await self.poll(ac))
        self.probe_device.assert_called_once()
        self.assertIn("(last error: Device returned error 0xfffb (-5))", logs.output[0])

    async def test_successful_write_restarts_the_failure_count(self):
        ac = self.make([TIMEOUT, TIMEOUT, TIMEOUT, TIMEOUT], [])
        await self.poll(ac)
        await self.poll(ac)
        await ac.async_set_param("pwr", 1)
        await self.poll(ac)
        await self.poll(ac)
        self.probe_device.assert_not_called()

    async def test_write_answered_during_the_probe_keeps_the_device_available(self):
        ac = self.make([TIMEOUT] * 3, [probe(direct=True, broadcast_host="192.0.2.10")])
        self.probe_gate = asyncio.Event()
        await self.poll(ac)
        await self.poll(ac)
        with self.assertNoLogs(coordinator._LOGGER, "WARNING"):
            probing_poll = asyncio.create_task(ac._async_update_data())
            await asyncio.sleep(0)
            self.probe_device.assert_called_once()
            await ac.async_set_param("pwr", 1)
            self.probe_gate.set()
            self.assertEqual(await probing_poll, {"pwr": 1})

    async def test_failed_probe_keeps_the_poll_error(self):
        ac = self.make([TIMEOUT] * 3, [OSError("Too many open files")])
        await self.poll(ac)
        await self.poll(ac)
        with self.assertRaisesRegex(UpdateFailed, "Timeout waiting for 192.0.2.10:80"):
            await ac._async_update_data()
        self.probe_device.assert_called_once()

    async def test_follows_device_to_new_address_and_stores_it(self):
        devices = [
            {"name": "Master", "host": "192.0.2.10", "mac": MAC, "key": KEY},
            {"name": "Bedroom", "host": "192.0.2.11", "mac": OTHER_MAC, "key": KEY},
        ]
        entry = FakeEntry({"devices": devices})
        ac = self.make([TIMEOUT, TIMEOUT, TIMEOUT, {"pwr": 1}], [probe(broadcast_host="192.0.2.20", arp_mac="")], entry)
        with self.assertLogs(coordinator._LOGGER, "WARNING") as logs:
            await self.poll(ac)
            await self.poll(ac)
            self.assertEqual(await self.poll(ac), {"pwr": 1})
        self.assertEqual(self.polled_hosts, ["192.0.2.10"] * 3 + ["192.0.2.20"])
        self.hass.config_entries.async_update_entry.assert_called_once_with(
            entry,
            data={"devices": [{**devices[0], "host": "192.0.2.20"}, devices[1]]},
        )
        self.assertIn("Master answered discovery at 192.0.2.20 instead of 192.0.2.10", logs.output[0])
        self.assertIn("Master at 192.0.2.20 is responding again after 1 min 0 s", logs.output[1])

    async def test_yaml_device_follows_new_address_without_an_entry(self):
        ac = self.make([TIMEOUT] * 4, [probe(broadcast_host="192.0.2.20")])
        with self.assertLogs(coordinator._LOGGER, "WARNING"):
            for _ in range(3):
                self.assertIsNone(await self.poll(ac))
        self.assertEqual(self.polled_hosts[-1], "192.0.2.20")
        self.hass.config_entries.async_update_entry.assert_not_called()


class EntryDataTests(unittest.TestCase):
    def test_replaces_only_the_matching_device(self):
        data = {"devices": [{"host": "a", "mac": MAC}, {"host": "b", "mac": OTHER_MAC}]}
        self.assertEqual(
            coordinator.replace_device_host(data, "020000000002", "c"),
            {"devices": [{"host": "a", "mac": MAC}, {"host": "c", "mac": OTHER_MAC}]},
        )
        self.assertEqual(data["devices"][1]["host"], "b")

    def test_single_device_entry_and_unknown_mac(self):
        data = {"host": "a", "mac": MAC.upper()}
        self.assertEqual(coordinator.replace_device_host(data, MAC, "c"), {"host": "c", "mac": MAC.upper()})
        self.assertIsNone(coordinator.replace_device_host(data, OTHER_MAC, "c"))
        self.assertIsNone(coordinator.replace_device_host({"devices": []}, MAC, "c"))


if __name__ == "__main__":
    unittest.main()
