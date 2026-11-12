"""Shared device runtime and coordinator for TCL Intelligent AC."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass, replace
from datetime import timedelta
from time import monotonic
from typing import Any

from homeassistant.config_entries import ConfigEntry, UnknownEntry
from homeassistant.const import CONF_HOST, CONF_NAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import CONF_DEVICE_ID, CONF_DEVICES, CONF_KEY, CONF_MAC, DOMAIN
from .protocol import (
    LINK_ADDRESS_TAKEN,
    LINK_ARP_ONLY,
    LINK_CONTROL_STUCK,
    LINK_MOVED,
    LINK_NO_BROADCAST,
    LINK_SILENT,
    LINK_UNREACHABLE,
    TclAcClient,
    TclAcDevice,
    TclAcError,
    diagnose_probe,
    probe_device,
)

_LOGGER = logging.getLogger(__name__)
SCAN_INTERVAL = timedelta(seconds=30)
# Failed polls in a row before the device is probed: about 1.5 minutes.
PROBE_AFTER_FAILED_POLLS = 3
# Probes run at most this often, also across short recoveries of a flapping link.
PROBE_INTERVAL = timedelta(minutes=10)

_DIAGNOSES = {
    LINK_CONTROL_STUCK: "it still answers discovery, so the module is online but its local control service is not working",
    LINK_NO_BROADCAST: (
        "it answers a discovery hello sent straight to it but not a broadcast one, so it may have stopped "
        "receiving Wi-Fi broadcasts, or broadcasts from Home Assistant do not reach its network"
    ),
    LINK_ARP_ONLY: "it answers ARP but not discovery, so the module is online but its local UDP service does not respond",
    LINK_ADDRESS_TAKEN: "the address now resolves to another MAC ({arp_mac}) and the AC did not answer a broadcast hello",
    LINK_UNREACHABLE: "it answers neither ARP nor discovery, so the AC is off, out of Wi-Fi range or not receiving broadcasts",
    LINK_SILENT: "it answers neither a direct nor a broadcast discovery hello",
}


class TclAcCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinator that polls one TCL AC and writes local parameters."""

    def __init__(
        self,
        hass: HomeAssistant,
        client: TclAcClient,
        name: str,
        entry: ConfigEntry | None = None,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN}_{client.device.mac}",
            update_interval=SCAN_INTERVAL,
        )
        self.client = client
        self.device_name = name
        self._failed_polls = 0
        self._failing_since = 0.0
        self._next_probe = 0.0
        self._reported: str | None = None
        self._answers = 0

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            data = await self.hass.async_add_executor_job(self.client.get_state)
        except TclAcError as exc:
            # Error replies count too: a different device may now hold the address.
            data = await self._async_note_failure(exc)
            if data is None:
                raise UpdateFailed(str(exc)) from exc
        self._note_answer()
        return data

    async def async_set_param(self, param: str, value: Any) -> None:
        data = await self.hass.async_add_executor_job(self.client.set_param, param, value)
        self._note_answer()
        self.async_set_updated_data(data)

    async def async_set_params(self, params: dict[str, Any]) -> None:
        data = await self.hass.async_add_executor_job(self.client.set_params, params)
        self._note_answer()
        self.async_set_updated_data(data)

    async def _async_note_failure(self, error: TclAcError) -> dict[str, Any] | None:
        """Count a failed poll and probe the device once it is due.

        Returns state when the device answered after all: a write succeeded
        while the probe ran, or the device answered from a new address.
        """

        now = monotonic()
        if not self._failed_polls:
            self._failing_since = now
        self._failed_polls += 1
        if self._failed_polls < PROBE_AFTER_FAILED_POLLS or now < self._next_probe:
            return None
        self._next_probe = now + PROBE_INTERVAL.total_seconds()

        device = self.client.device
        answers = self._answers
        try:
            probe = await self.hass.async_add_executor_job(probe_device, device.host, device.mac)
        except Exception:  # noqa: BLE001 - the probe only adds a diagnosis
            _LOGGER.debug("Could not probe %s at %s", self.device_name, device.host, exc_info=True)
            return None
        if self._answers != answers:
            return self.data
        diagnosis = diagnose_probe(probe, device.mac)
        if diagnosis == LINK_MOVED:
            return await self._async_follow(probe.broadcast_host or device.host)
        if diagnosis != self._reported:
            self._reported = diagnosis
            _LOGGER.warning(
                "%s at %s has not returned its state for %s (last error: %s): %s",
                self.device_name,
                device.host,
                _duration(now - self._failing_since),
                error,
                _DIAGNOSES[diagnosis].format(arp_mac=probe.arp_mac),
            )
        return None

    async def _async_follow(self, host: str) -> dict[str, Any] | None:
        """Poll the device at the address it answered discovery from."""

        _LOGGER.warning(
            "%s answered discovery at %s instead of %s; polling the new address",
            self.device_name,
            host,
            self.client.device.host,
        )
        self._reported = LINK_MOVED
        self.client.device = replace(self.client.device, host=host)
        if (entry := self.config_entry) is not None:
            data = replace_device_host(entry.data, self.client.device.mac, host)
            # The entry may have been removed while the probe ran.
            if data is not None:
                with suppress(UnknownEntry):
                    self.hass.config_entries.async_update_entry(entry, data=data)
        try:
            return await self.hass.async_add_executor_job(self.client.get_state)
        except TclAcError:
            return None

    def _note_answer(self) -> None:
        """Close a failure episode after the device answered with its state."""

        self._answers += 1
        if self._reported is not None:
            _LOGGER.warning(
                "%s at %s is responding again after %s",
                self.device_name,
                self.client.device.host,
                _duration(monotonic() - self._failing_since),
            )
        self._failed_polls = 0
        self._reported = None


@dataclass(frozen=True)
class TclAcRuntime:
    """Runtime objects shared by TCL AC entities."""

    name: str
    coordinator: TclAcCoordinator
    device_info: dict[str, Any]
    unique_id: str


def runtime_from_config(
    hass: HomeAssistant,
    device: dict[str, Any],
    entry: ConfigEntry | None = None,
) -> TclAcRuntime:
    """Build a shared runtime from a stored device config."""

    client = TclAcClient(
        TclAcDevice(
            host=device[CONF_HOST],
            mac=device[CONF_MAC],
            key=device[CONF_KEY],
            device_id=int(device.get(CONF_DEVICE_ID, 1)),
        )
    )
    compact_mac = _mac_key(client.device.mac)
    name = device[CONF_NAME]
    return TclAcRuntime(
        name=name,
        coordinator=TclAcCoordinator(hass, client, name, entry),
        device_info={
            "identifiers": {(DOMAIN, client.device.mac.lower())},
            "manufacturer": "TCL",
            "model": "XA71I",
            "name": name,
        },
        unique_id=f"{DOMAIN}_{compact_mac}",
    )


def replace_device_host(data: Mapping[str, Any], mac: str, host: str) -> dict[str, Any] | None:
    """Return config entry data with the host of the device with this MAC replaced.

    Returns None when the entry does not hold the device.
    """

    target = _mac_key(mac)
    if CONF_DEVICES in data:
        devices = [dict(device) for device in data[CONF_DEVICES]]
        matches = [device for device in devices if _mac_key(device[CONF_MAC]) == target]
        if not matches:
            return None
        for device in matches:
            device[CONF_HOST] = host
        return {**data, CONF_DEVICES: devices}
    if data.get(CONF_MAC) and _mac_key(data[CONF_MAC]) == target:
        return {**data, CONF_HOST: host}
    return None


def _mac_key(mac: str) -> str:
    return mac.replace(":", "").replace("-", "").lower()


def _duration(seconds: float) -> str:
    minutes, seconds = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours} h {minutes} min"
    if minutes:
        return f"{minutes} min {seconds} s"
    return f"{seconds} s"
