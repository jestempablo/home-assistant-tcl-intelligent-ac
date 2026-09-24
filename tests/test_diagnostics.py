"""Exercise the actual diagnostics hook without starting Home Assistant."""

from __future__ import annotations

import importlib
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest

# Supply a package namespace only: importing the integration's __init__ would
# start loading HA platforms unrelated to these pure, cached diagnostics tests.
PACKAGE_NAME = "tcl_diagnostics_test"
package = ModuleType(PACKAGE_NAME)
package.__path__ = [
    str(Path(__file__).resolve().parents[1] / "custom_components" / "tcl_intelligent_ac")
]
sys.modules[PACKAGE_NAME] = package
diagnostics = importlib.import_module(f"{PACKAGE_NAME}.diagnostics")


class EntryWithoutConfigAccess:
    entry_id = "test-entry"

    @property
    def data(self):
        raise AssertionError("Diagnostics must not read stored config or keys")


class DiagnosticsTests(unittest.IsolatedAsyncioTestCase):
    async def get_diagnostics(self, states, successes=None):
        if successes is None:
            successes = [True] * len(states)
        runtimes = [
            SimpleNamespace(
                coordinator=SimpleNamespace(data=state, last_update_success=success)
            )
            for state, success in zip(states, successes)
        ]
        hass = SimpleNamespace(data={diagnostics.DOMAIN: {"test-entry": runtimes}})
        # The runtimes deliberately have no client or device metadata. Reading
        # either would fail, as would a network refresh through the coordinator.
        return await diagnostics.async_get_config_entry_diagnostics(hass, EntryWithoutConfigAccess())

    async def test_only_control_flags_leave_the_hook(self):
        state = {
            "pwr": 1, "tcl_mode": 3, "smartdesic": 0, "desicmode": 1,
            "tcl_vdir": 7, "tcl_hdir": 1,
            "key": "SYNTHETIC-SECRET", "token": "SYNTHETIC-TOKEN",
            "mac": "SYNTHETIC-MAC", "host": "SYNTHETIC-HOST",
            "name": "SYNTHETIC-NAME", "temp": 240, "envtemp": 25,
            "unexpected": {"password": "SYNTHETIC-PASSWORD"},
        }
        result = await self.get_diagnostics([state])
        self.assertEqual(result["devices"][0]["state"], {
            "pwr": 1, "tcl_mode": 3, "smartdesic": 0, "desicmode": 1,
            "tcl_vdir": 7, "tcl_hdir": 1,
        })
        self.assertNotIn("SYNTHETIC", json.dumps(result))
        self.assertEqual(state["key"], "SYNTHETIC-SECRET")

    async def test_rejects_strings_containers_and_nonfinite_values(self):
        for value in ("SYNTHETIC-SECRET", {"key": "secret"}, [1], float("nan"), float("inf")):
            with self.subTest(value=value):
                result = await self.get_diagnostics([{"smartdesic": value}])
                self.assertEqual(result["devices"][0]["state"], {})
                json.dumps(result, allow_nan=False)

    async def test_preserves_unavailable_and_unmapped_numeric_codes(self):
        state = {"smartdesic": -1, "desicmode": None, "tcl_vdir": 99, "tcl_hdir": 2.0, "pwr": False}
        result = await self.get_diagnostics([state])
        self.assertEqual(result["devices"][0]["state"], state)

    async def test_keeps_devices_separate_and_reports_failed_poll(self):
        result = await self.get_diagnostics([{"pwr": 1}, {"pwr": 0}], [True, False])
        self.assertEqual(result["source"], "cached_state")
        self.assertEqual(result["devices"], [
            {"device_index": 1, "last_update_success": True, "state": {"pwr": 1}},
            {"device_index": 2, "last_update_success": False, "state": {"pwr": 0}},
        ])

    async def test_unloaded_entry_and_missing_state(self):
        for data in ({}, {diagnostics.DOMAIN: {}}):
            result = await diagnostics.async_get_config_entry_diagnostics(
                SimpleNamespace(data=data), EntryWithoutConfigAccess()
            )
            self.assertEqual(result["devices"], [])
        result = await self.get_diagnostics([None], [False])
        self.assertEqual(result["devices"][0]["state"], {})


if __name__ == "__main__":
    unittest.main()
