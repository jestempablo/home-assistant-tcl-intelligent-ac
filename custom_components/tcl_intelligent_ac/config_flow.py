"""Config flow for TCL Intelligent AC."""

from __future__ import annotations

import hashlib
import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_NAME, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv

from .cloud import (
    CLOUD_REGIONS,
    CloudDevice,
    TclCloudAuthError,
    TclCloudError,
    TclCloudRateLimitError,
    get_cloud_devices,
)
from .const import CONF_DEVICES, CONF_KEY, CONF_MAC, DOMAIN
from .protocol import TclAcClient, TclAcDevice

CONF_REGION = "region"
CONF_SELECTION = "selection"
CONF_SETUP_METHOD = "setup_method"
SETUP_METHOD_CLOUD = "cloud"
SETUP_METHOD_MANUAL = "manual"

_LOGGER = logging.getLogger(__name__)


def _mac_unique_id(mac: str) -> str:
    return mac.replace(":", "").replace("-", "").lower()


def _manual_schema(defaults: dict[str, Any] | None = None) -> vol.Schema:
    defaults = defaults or {}
    return vol.Schema(
        {
            vol.Required(CONF_NAME, default=defaults.get(CONF_NAME, "")): str,
            vol.Required(CONF_HOST, default=defaults.get(CONF_HOST, "")): str,
            vol.Required(CONF_MAC, default=defaults.get(CONF_MAC, "")): str,
            vol.Required(CONF_KEY, default=defaults.get(CONF_KEY, "")): str,
        }
    )


def _cloud_schema(defaults: dict[str, Any] | None = None) -> vol.Schema:
    defaults = defaults or {}
    return vol.Schema(
        {
            vol.Required(CONF_USERNAME, default=defaults.get(CONF_USERNAME, "")): str,
            vol.Required(CONF_PASSWORD): str,
            vol.Required(CONF_REGION, default=defaults.get(CONF_REGION, "us")): vol.In(
                {key: region.label for key, region in CLOUD_REGIONS.items()}
            ),
        }
    )


def _user_schema(defaults: dict[str, Any] | None = None) -> vol.Schema:
    defaults = defaults or {}
    return vol.Schema(
        {
            vol.Required(
                CONF_SETUP_METHOD,
                default=defaults.get(CONF_SETUP_METHOD, SETUP_METHOD_CLOUD),
            ): vol.In(
                {
                    SETUP_METHOD_CLOUD: "Use Intelligent AC account",
                    SETUP_METHOD_MANUAL: "Enter LAN details manually",
                }
            )
        }
    )


def _select_schema(devices: list[CloudDevice]) -> vol.Schema:
    options = {
        device.mac: f"{device.name} ({device.mac}{', ' + device.host if device.host else ', host not found'})"
        for device in devices
    }
    return vol.Schema({vol.Required(CONF_SELECTION, default=list(options)): cv.multi_select(options)})


async def _validate_input(hass: HomeAssistant, data: dict[str, Any]) -> None:
    client = TclAcClient(TclAcDevice(host=data[CONF_HOST], mac=data[CONF_MAC], key=data[CONF_KEY]))
    await hass.async_add_executor_job(client.get_state)


async def _validate_devices(hass: HomeAssistant, devices: list[dict[str, Any]]) -> None:
    for device in devices:
        if not device.get(CONF_HOST):
            raise TclCloudError("Could not find the device host on the LAN")
        await _validate_input(hass, device)


def _configured_macs(hass: HomeAssistant) -> set[str]:
    macs: set[str] = set()
    for entry in hass.config_entries.async_entries(DOMAIN):
        if CONF_DEVICES in entry.data:
            macs.update(_mac_unique_id(device[CONF_MAC]) for device in entry.data[CONF_DEVICES])
        elif entry.data.get(CONF_MAC):
            macs.add(_mac_unique_id(entry.data[CONF_MAC]))
    return macs


def _cloud_unique_id(devices: list[dict[str, Any]]) -> str:
    macs = ",".join(sorted(_mac_unique_id(device[CONF_MAC]) for device in devices))
    return "cloud_" + hashlib.sha1(macs.encode()).hexdigest()[:16]  # noqa: S324


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for TCL Intelligent AC."""

    VERSION = 1

    def __init__(self) -> None:
        self._cloud_devices: list[CloudDevice] = []

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> config_entries.ConfigFlowResult:
        """Handle the initial step."""

        if user_input is not None:
            if user_input[CONF_SETUP_METHOD] == SETUP_METHOD_MANUAL:
                return await self.async_step_manual()
            return await self.async_step_cloud()

        return self.async_show_form(step_id="user", data_schema=_user_schema(user_input), errors={})

    async def async_step_cloud(self, user_input: dict[str, Any] | None = None) -> config_entries.ConfigFlowResult:
        """Handle cloud-assisted device discovery."""

        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                self._cloud_devices = await self.hass.async_add_executor_job(
                    get_cloud_devices,
                    user_input[CONF_USERNAME],
                    user_input[CONF_PASSWORD],
                    user_input[CONF_REGION],
                )
            except TclCloudRateLimitError as exc:
                _LOGGER.warning("TCL Intelligent AC cloud temporarily blocked login attempts: %s", exc)
                errors["base"] = "too_many_attempts"
            except TclCloudAuthError as exc:
                _LOGGER.warning("TCL Intelligent AC cloud rejected the credentials: %s", exc)
                errors["base"] = "invalid_auth"
            except TclCloudError as exc:
                _LOGGER.warning("TCL Intelligent AC cloud setup failed: %s", exc)
                errors["base"] = "cloud_error"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Unexpected TCL Intelligent AC cloud setup error")
                errors["base"] = "cloud_error"
            else:
                if not self._cloud_devices:
                    errors["base"] = "no_devices"
                else:
                    return await self.async_step_select()

        return self.async_show_form(step_id="cloud", data_schema=_cloud_schema(user_input), errors=errors)

    async def async_step_select(self, user_input: dict[str, Any] | None = None) -> config_entries.ConfigFlowResult:
        """Let the user choose cloud-discovered devices."""

        errors: dict[str, str] = {}

        if user_input is not None:
            selected_macs = set(user_input.get(CONF_SELECTION) or [])
            selected = [device.as_config() for device in self._cloud_devices if device.mac in selected_macs]
            if not selected:
                errors["base"] = "no_devices_selected"
            elif _configured_macs(self.hass).intersection(_mac_unique_id(device[CONF_MAC]) for device in selected):
                errors["base"] = "already_configured"
            else:
                try:
                    await _validate_devices(self.hass, selected)
                except TclCloudError:
                    errors["base"] = "host_not_found"
                except Exception:  # noqa: BLE001
                    errors["base"] = "cannot_connect"
                else:
                    await self.async_set_unique_id(_cloud_unique_id(selected))
                    self._abort_if_unique_id_configured()
                    title = selected[0][CONF_NAME] if len(selected) == 1 else f"TCL Intelligent AC ({len(selected)} devices)"
                    return self.async_create_entry(title=title, data={CONF_DEVICES: selected})

        return self.async_show_form(step_id="select", data_schema=_select_schema(self._cloud_devices), errors=errors)

    async def async_step_manual(self, user_input: dict[str, Any] | None = None) -> config_entries.ConfigFlowResult:
        """Handle manual LAN setup."""

        errors: dict[str, str] = {}

        if user_input is not None:
            unique_id = _mac_unique_id(user_input[CONF_MAC])
            await self.async_set_unique_id(unique_id)
            self._abort_if_unique_id_configured(updates={CONF_HOST: user_input[CONF_HOST]})
            if unique_id in _configured_macs(self.hass):
                errors["base"] = "already_configured"
            else:
                try:
                    await _validate_input(self.hass, user_input)
                except Exception:  # noqa: BLE001
                    errors["base"] = "cannot_connect"
                else:
                    return self.async_create_entry(title=user_input[CONF_NAME], data=user_input)

        return self.async_show_form(step_id="manual", data_schema=_manual_schema(user_input), errors=errors)
