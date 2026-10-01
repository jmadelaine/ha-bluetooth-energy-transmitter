"""Diagnostics: adapters, their advertising capabilities, and saved signals."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import BluetoothEnergyTransmitterConfigEntry
from .bluez import BroadcastError
from .const import CONF_DATA, CONF_PAYLOAD, CONF_SERVICE_DATA

# Payloads can be device tokens; keep them out of shared diagnostics.
TO_REDACT = {CONF_PAYLOAD, CONF_SERVICE_DATA, CONF_DATA}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: BluetoothEnergyTransmitterConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for the config entry."""
    broadcaster = entry.runtime_data
    try:
        adapters: Any = [
            adapter.as_dict() for adapter in await broadcaster.async_get_adapters()
        ]
    except BroadcastError as err:
        adapters = f"error: {err}"
    return {
        "options": dict(entry.options),
        "active_broadcasts": broadcaster.active_count,
        "adapters": adapters,
        "signals": [
            {
                "title": subentry.title,
                "data": async_redact_data(dict(subentry.data), TO_REDACT),
            }
            for subentry in entry.subentries.values()
        ],
    }
