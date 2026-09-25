"""Select entities for TCL Intelligent AC."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.select import SelectEntity, SelectEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .airflow import HORIZONTAL, VERTICAL, airflow_command, airflow_options
from .const import DOMAIN
from .coordinator import TclAcCoordinator, TclAcRuntime

SLEEP_OPTIONS = {
    0: "off",
    1: "normal",
    2: "senior",
    3: "child",
    4: "custom",
}


@dataclass(frozen=True, kw_only=True)
class TclAcSelectDescription(SelectEntityDescription):
    """Description for a TCL AC select."""

    param: str
    options_by_code: dict[int, str]
    airflow_axis: bool = False


SELECT_DESCRIPTIONS: tuple[TclAcSelectDescription, ...] = (
    TclAcSelectDescription(
        key="sleep",
        name="Sleep",
        icon="mdi:sleep",
        param="tcl_slp",
        options_by_code=SLEEP_OPTIONS,
    ),
    TclAcSelectDescription(
        key="vertical_airflow",
        name="Vertical airflow",
        icon="mdi:arrow-up-down",
        param=VERTICAL,
        options_by_code={},
        airflow_axis=True,
    ),
    TclAcSelectDescription(
        key="horizontal_airflow",
        name="Horizontal airflow",
        icon="mdi:arrow-left-right",
        param=HORIZONTAL,
        options_by_code={},
        airflow_axis=True,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up TCL Intelligent AC selects from a config entry."""

    async_add_entities(
        [
            TclAcSelect(runtime, description)
            for runtime in hass.data[DOMAIN][entry.entry_id]
            for description in SELECT_DESCRIPTIONS
        ],
        update_before_add=True,
    )


class TclAcSelect(CoordinatorEntity[TclAcCoordinator], SelectEntity):
    """Select for a TCL AC enum parameter."""

    _attr_has_entity_name = True

    entity_description: TclAcSelectDescription

    def __init__(self, runtime: TclAcRuntime, description: TclAcSelectDescription) -> None:
        super().__init__(runtime.coordinator)
        self.entity_description = description
        self._attr_device_info = runtime.device_info
        self._attr_unique_id = f"{runtime.unique_id}_{description.key}"

    @property
    def _options_by_code(self) -> dict[int, str]:
        if self.entity_description.airflow_axis:
            return airflow_options(self.coordinator.data or {}, self.entity_description.param)
        return self.entity_description.options_by_code

    @property
    def options(self) -> list[str]:
        """Return the options supported by the current capability flags."""
        return list(self._options_by_code.values())

    @property
    def available(self) -> bool:
        """Return whether this parameter is supported and the device is available."""

        return super().available and self._raw_value not in (None, -1)

    @property
    def current_option(self) -> str | None:
        """Return selected option."""

        value = self._raw_value
        if value in (None, -1):
            return None
        return self._options_by_code.get(value)

    @property
    def _raw_value(self) -> Any:
        return (self.coordinator.data or {}).get(self.entity_description.param)

    async def async_select_option(self, option: str) -> None:
        """Select an option."""

        if self.entity_description.airflow_axis:
            try:
                params = airflow_command(
                    self.coordinator.data or {}, self.entity_description.param, option
                )
            except ValueError as exc:
                raise ServiceValidationError(str(exc)) from exc
            await self.coordinator.async_set_params(params)
            return
        code_by_option = {value: key for key, value in self._options_by_code.items()}
        await self.coordinator.async_set_param(self.entity_description.param, code_by_option[option])
