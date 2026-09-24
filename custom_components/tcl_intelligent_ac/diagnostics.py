"""Limited, cached diagnostics for investigating TCL control mappings."""

from __future__ import annotations

from math import isfinite
from typing import TYPE_CHECKING, Any

from .const import DOMAIN

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

# Only control flags needed to compare app settings are exported. Never copy
# entry.data, device metadata, exceptions, arbitrary keys or nested payloads.
CONTROL_FIELDS = frozenset(
    {
        "pwr", "tcl_mode", "tcl_mark", "tcl_vdir", "tcl_hdir", "3dairmode",
        "smartdesic", "desicmode", "evaportor", "ac_health", "tcl_slp",
        "pwfmode", "qtmode", "ecomode", "bglight", "beep", "8heat",
    }
)


def _control_state(data: Any) -> dict[str, Any]:
    """Keep only known control fields containing simple numeric values."""
    if not isinstance(data, dict):
        return {}
    state = {}
    for key in sorted(CONTROL_FIELDS):
        if key not in data:
            continue
        value = data[key]
        if (
            value is None
            or type(value) in (bool, int)
            or (type(value) is float and isfinite(value))
        ):
            state[key] = value
    return state


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return cached control flags without reading config or contacting an AC."""
    runtimes = hass.data.get(DOMAIN, {}).get(entry.entry_id, [])
    return {
        "source": "cached_state",
        "devices": [
            {
                "device_index": index,
                "last_update_success": runtime.coordinator.last_update_success,
                "state": _control_state(runtime.coordinator.data),
            }
            for index, runtime in enumerate(runtimes, start=1)
        ],
    }
