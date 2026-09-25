"""Switch entities for TCL Intelligent AC extra features."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import TclAcCoordinator, TclAcRuntime


@dataclass(frozen=True, kw_only=True)
class TclAcSwitchDescription(SwitchEntityDescription):
    """Description for a TCL AC switch."""

    param: str


SWITCH_DESCRIPTIONS: tuple[TclAcSwitchDescription, ...] = (
    TclAcSwitchDescription(key="clean", name="Evaporator clean", icon="mdi:air-filter", param="evaportor"),
    TclAcSwitchDescription(key="turbo", name="Turbo", icon="mdi:fan-chevron-up", param="pwfmode"),
    TclAcSwitchDescription(key="eco", name="Eco", icon="mdi:leaf", param="ecomode"),
    TclAcSwitchDescription(key="quiet", name="Quiet", icon="mdi:volume-low", param="qtmode"),
    TclAcSwitchDescription(key="display", name="Display", icon="mdi:television-ambient-light", param="bglight"),
    TclAcSwitchDescription(key="buzzer", name="Buzzer", icon="mdi:volume-high", param="beep"),
    # Keep the legacy key/unique_id and its command so existing automations do
    # not silently change function. Only correct its misleading display name.
    TclAcSwitchDescription(key="anti_mildew", name="Smart dehumidification", icon="mdi:water-percent", param="smartdesic"),
    TclAcSwitchDescription(key="after_run_drying", name="Anti-mildew (after-run drying)", icon="mdi:shield-check-outline", param="desicmode"),
    TclAcSwitchDescription(key="health", name="Health", icon="mdi:heart-pulse", param="ac_health"),
    TclAcSwitchDescription(key="eight_degree_heat", name="Frost protection", icon="mdi:snowflake-thermometer", param="8heat"),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up TCL Intelligent AC switches from a config entry."""

    async_add_entities(
        [
            TclAcSwitch(runtime, description)
            for runtime in hass.data[DOMAIN][entry.entry_id]
            for description in SWITCH_DESCRIPTIONS
        ],
        update_before_add=True,
    )


class TclAcSwitch(CoordinatorEntity[TclAcCoordinator], SwitchEntity):
    """Switch for a TCL AC boolean parameter."""

    _attr_has_entity_name = True

    entity_description: TclAcSwitchDescription

    def __init__(self, runtime: TclAcRuntime, description: TclAcSwitchDescription) -> None:
        super().__init__(runtime.coordinator)
        self.entity_description = description
        self._attr_device_info = runtime.device_info
        self._attr_unique_id = f"{runtime.unique_id}_{description.key}"

    @property
    def available(self) -> bool:
        """Return whether this parameter is supported and the device is available."""

        return super().available and self._raw_value not in (None, -1)

    @property
    def is_on(self) -> bool | None:
        """Return whether the feature is enabled."""

        value = self._raw_value
        if value in (None, -1):
            return None
        return value == 1

    @property
    def _raw_value(self) -> Any:
        return (self.coordinator.data or {}).get(self.entity_description.param)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the feature on."""

        if self.entity_description.param == "desicmode":
            state = self.coordinator.data or {}
            if state.get("pwr") != 1 or state.get("tcl_mode") not in (2, 3):
                raise ServiceValidationError(
                    "Anti-mildew can only be armed while the AC is cooling or drying."
                )
        await self.coordinator.async_set_param(self.entity_description.param, 1)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the feature off."""

        await self.coordinator.async_set_param(self.entity_description.param, 0)
