"""Regression tests using real HA entity classes and a mocked device boundary."""

from __future__ import annotations

import importlib
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from homeassistant.exceptions import ServiceValidationError

# Load the actual platforms without setting up HA or opening device sockets.
PACKAGE_NAME = "tcl_controls_test"
package = ModuleType(PACKAGE_NAME)
package.__path__ = [
    str(Path(__file__).resolve().parents[1] / "custom_components" / "tcl_intelligent_ac")
]
sys.modules[PACKAGE_NAME] = package
select = importlib.import_module(f"{PACKAGE_NAME}.select")
switch = importlib.import_module(f"{PACKAGE_NAME}.switch")
climate = importlib.import_module(f"{PACKAGE_NAME}.climate")


def runtime(**state):
    return SimpleNamespace(
        unique_id="test_ac", device_info={},
        coordinator=SimpleNamespace(
            data=state, last_update_success=True,
            async_set_param=AsyncMock(), async_set_params=AsyncMock(),
        ),
    )


def select_entity(device, key):
    description = next(d for d in select.SELECT_DESCRIPTIONS if d.key == key)
    return select.TclAcSelect(device, description)


def switch_entity(device, key):
    description = next(d for d in switch.SWITCH_DESCRIPTIONS if d.key == key)
    return switch.TclAcSwitch(device, description)


class AirflowTests(unittest.IsolatedAsyncioTestCase):
    async def test_climate_advertises_and_implements_power_services(self):
        device = runtime(pwr=0, tcl_mode=3)
        entity = climate.TclIntelligentAcClimate(device)
        self.assertTrue(entity.supported_features & climate.ClimateEntityFeature.TURN_ON)
        self.assertTrue(entity.supported_features & climate.ClimateEntityFeature.TURN_OFF)
        await entity.async_turn_on()
        device.coordinator.async_set_param.assert_awaited_with("pwr", 1)
        await entity.async_turn_off()
        device.coordinator.async_set_param.assert_awaited_with("pwr", 0)

    async def test_basic_devices_use_boolean_swing_and_unknown_flags_keep_legacy(self):
        for flags in (None, -1, 0, 4, "128", True):
            with self.subTest(flags=flags):
                device = runtime(if_function=flags, tcl_vdir=0, tcl_hdir=0)
                for key, param, code in (
                    ("vertical_airflow", "tcl_vdir", 1 if type(flags) is int and flags >= 0 else 7),
                    ("horizontal_airflow", "tcl_hdir", 1),
                ):
                    entity = select_entity(device, key)
                    self.assertEqual(entity.options, ["off", "full swing"])
                    await entity.async_select_option("full swing")
                    device.coordinator.async_set_params.assert_awaited_with({param: code})
                    with self.assertRaises(ServiceValidationError):
                        await entity.async_select_option("middle fixed")

    async def test_precision_vertical_options_write_only_vertical_axis(self):
        device = runtime(if_function=128, tcl_vdir=0, tcl_hdir=12)
        entity = select_entity(device, "vertical_airflow")
        # Independent oracle transcribed from the official 7c500000 UI profile.
        expected = {
            "off": 0, "full swing": 7, "upper swing": 8, "lower swing": 9,
            "top fixed": 1, "upper fixed": 2, "middle fixed": 3,
            "lower fixed": 4, "bottom fixed": 5,
        }
        self.assertEqual(set(entity.options), set(expected))
        for option, code in expected.items():
            await entity.async_select_option(option)
            device.coordinator.async_set_params.assert_awaited_with({"tcl_vdir": code})
            device.coordinator.data["tcl_vdir"] = code
            self.assertEqual(entity.current_option, option)
            self.assertEqual(device.coordinator.data["tcl_hdir"], 12)

    async def test_precision_horizontal_and_wide_capabilities(self):
        device = runtime(if_function=128, tcl_vdir=8, tcl_hdir=0)
        entity = select_entity(device, "horizontal_airflow")
        normal = {
            "off": 0, "full swing": 10, "left swing": 11, "middle swing": 12,
            "right swing": 13, "left fixed": 1, "center-left fixed": 2,
            "middle fixed": 3, "center-right fixed": 4, "right fixed": 5,
        }
        wide = {
            "wide swing": 14, "center-left swing": 15, "center-right swing": 16,
            "left wide fixed": 6, "right wide fixed": 7, "whole angle fixed": 8,
        }
        self.assertEqual(set(entity.options), set(normal))
        with self.assertRaises(ServiceValidationError):
            await entity.async_select_option("wide swing")
        device.coordinator.async_set_params.assert_not_awaited()
        device.coordinator.data["if_function"] |= 4
        self.assertEqual(set(entity.options), set(normal | wide))
        for option, code in (normal | wide).items():
            await entity.async_select_option(option)
            device.coordinator.async_set_params.assert_awaited_with({"tcl_hdir": code})
            device.coordinator.data["tcl_hdir"] = code
            self.assertEqual(entity.current_option, option)
            self.assertEqual(device.coordinator.data["tcl_vdir"], 8)

    async def test_unavailable_unknown_and_failed_poll_states(self):
        device = runtime(if_function=128)
        entity = select_entity(device, "vertical_airflow")
        for raw in (None, -1):
            device.coordinator.data["tcl_vdir"] = raw
            self.assertFalse(entity.available)
            self.assertIsNone(entity.current_option)
        device.coordinator.data["tcl_vdir"] = 99
        self.assertTrue(entity.available)
        self.assertIsNone(entity.current_option)
        device.coordinator.last_update_success = False
        self.assertFalse(entity.available)

    async def test_combined_climate_control_keeps_api_and_uses_correct_profile(self):
        for flags, vertical_code, horizontal_code in ((0, 1, 1), (128, 7, 10), (None, 7, 1)):
            device = runtime(if_function=flags)
            entity = climate.TclIntelligentAcClimate(device)
            self.assertEqual(entity.swing_modes, ["off", "vertical", "horizontal", "both"])
            for mode, vertical, horizontal in (
                ("off", 0, 0), ("vertical", vertical_code, 0),
                ("horizontal", 0, horizontal_code), ("both", vertical_code, horizontal_code),
            ):
                await entity.async_set_swing_mode(mode)
                device.coordinator.async_set_params.assert_awaited_with(
                    {"tcl_vdir": vertical, "tcl_hdir": horizontal}
                )
                device.coordinator.data.update(tcl_vdir=vertical, tcl_hdir=horizontal)
                self.assertEqual(entity.swing_mode, mode)

    async def test_climate_distinguishes_fixed_positions_and_restricted_swing(self):
        device = runtime(if_function=132, tcl_vdir=3, tcl_hdir=1)
        entity = climate.TclIntelligentAcClimate(device)
        self.assertEqual(entity.swing_mode, "off")
        for vertical in (7, 8, 9):
            device.coordinator.data["tcl_vdir"] = vertical
            self.assertEqual(entity.swing_mode, "vertical")
        for horizontal in (10, 11, 12, 13, 14, 15, 16):
            device.coordinator.data["tcl_hdir"] = horizontal
            self.assertEqual(entity.swing_mode, "both")
        device.coordinator.data["tcl_vdir"] = 0
        self.assertEqual(entity.swing_mode, "horizontal")

    async def test_sleep_select_keeps_existing_options_and_command(self):
        device = runtime(tcl_slp=2)
        entity = select_entity(device, "sleep")
        self.assertEqual(entity.unique_id, "test_ac_sleep")
        self.assertEqual(entity.current_option, "senior")
        self.assertEqual(entity.options, ["off", "normal", "senior", "child", "custom"])
        await entity.async_select_option("child")
        device.coordinator.async_set_param.assert_awaited_once_with("tcl_slp", 3)


class DryingTests(unittest.IsolatedAsyncioTestCase):
    async def test_legacy_entity_keeps_its_identity_and_smartdesic_command(self):
        device = runtime(smartdesic=0, desicmode=0)
        entity = switch_entity(device, "anti_mildew")
        self.assertEqual(entity.unique_id, "test_ac_anti_mildew")
        self.assertEqual(entity.name, "Smart dehumidification")
        await entity.async_turn_on()
        device.coordinator.async_set_param.assert_awaited_once_with("smartdesic", 1)
        self.assertFalse(switch_entity(device, "after_run_drying").is_on)

    async def test_after_run_drying_arms_in_cool_and_dry(self):
        for mode in (2, 3):
            device = runtime(pwr=1, tcl_mode=mode, desicmode=0)
            entity = switch_entity(device, "after_run_drying")
            self.assertEqual(entity.unique_id, "test_ac_after_run_drying")
            await entity.async_turn_on()
            device.coordinator.async_set_param.assert_awaited_once_with("desicmode", 1)

    async def test_after_run_drying_rejects_off_and_other_modes_without_writing(self):
        for power, mode in ((0, 3), (0, 2), (1, 1), (1, 4), (1, 5), (1, None)):
            device = runtime(pwr=power, tcl_mode=mode, desicmode=0)
            entity = switch_entity(device, "after_run_drying")
            with self.assertRaises(ServiceValidationError):
                await entity.async_turn_on()
            device.coordinator.async_set_param.assert_not_awaited()
            await entity.async_turn_off()
            device.coordinator.async_set_param.assert_awaited_once_with("desicmode", 0)

    async def test_firmware_reset_is_reflected_without_automatic_rearming(self):
        device = runtime(pwr=1, tcl_mode=3, desicmode=1)
        entity = switch_entity(device, "after_run_drying")
        self.assertTrue(entity.is_on)
        device.coordinator.data.update(pwr=0, desicmode=0)
        self.assertFalse(entity.is_on)
        device.coordinator.async_set_param.assert_not_awaited()
        for raw in (-1, None):
            device.coordinator.data["desicmode"] = raw
            self.assertFalse(entity.available)
            self.assertIsNone(entity.is_on)


if __name__ == "__main__":
    unittest.main()
