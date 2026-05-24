"""Shared device runtime and coordinator for TCL Intelligent AC."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from homeassistant.const import CONF_HOST, CONF_NAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import CONF_KEY, CONF_MAC, DOMAIN
from .protocol import TclAcClient, TclAcDevice, TclAcError

_LOGGER = logging.getLogger(__name__)
SCAN_INTERVAL = timedelta(seconds=30)


class TclAcCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinator that polls one TCL AC and writes local parameters."""

    def __init__(self, hass: HomeAssistant, client: TclAcClient, name: str) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{client.device.mac}",
            update_interval=SCAN_INTERVAL,
        )
        self.client = client
        self.device_name = name

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            return await self.hass.async_add_executor_job(self.client.get_state)
        except TclAcError as exc:
            raise UpdateFailed(str(exc)) from exc

    async def async_set_param(self, param: str, value: Any) -> None:
        data = await self.hass.async_add_executor_job(self.client.set_param, param, value)
        self.async_set_updated_data(data)

    async def async_set_params(self, params: dict[str, Any]) -> None:
        data = await self.hass.async_add_executor_job(self.client.set_params, params)
        self.async_set_updated_data(data)


@dataclass(frozen=True)
class TclAcRuntime:
    """Runtime objects shared by TCL AC entities."""

    name: str
    coordinator: TclAcCoordinator
    device_info: dict[str, Any]
    unique_id: str


def runtime_from_config(hass: HomeAssistant, device: dict[str, Any]) -> TclAcRuntime:
    """Build a shared runtime from a stored device config."""

    client = TclAcClient(
        TclAcDevice(
            host=device[CONF_HOST],
            mac=device[CONF_MAC],
            key=device[CONF_KEY],
        )
    )
    compact_mac = client.device.mac.replace(":", "").replace("-", "").lower()
    name = device[CONF_NAME]
    return TclAcRuntime(
        name=name,
        coordinator=TclAcCoordinator(hass, client, name),
        device_info={
            "identifiers": {(DOMAIN, client.device.mac.lower())},
            "manufacturer": "TCL",
            "model": "XA71I",
            "name": name,
        },
        unique_id=f"{DOMAIN}_{compact_mac}",
    )
