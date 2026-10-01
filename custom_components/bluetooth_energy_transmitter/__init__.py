"""Broadcast user-defined BLE advertisements from the Home Assistant host."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .bluez import Broadcaster, BroadcastError
from .const import ADAPTER_AUTO, CONF_ADAPTER, DOMAIN
from .services import async_setup_services

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.BUTTON]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

type BluetoothEnergyTransmitterConfigEntry = ConfigEntry[Broadcaster]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the send action."""
    async_setup_services(hass)
    return True


async def async_setup_entry(
    hass: HomeAssistant, entry: BluetoothEnergyTransmitterConfigEntry
) -> bool:
    """Check BlueZ is reachable, then set up a button per saved signal."""
    broadcaster = Broadcaster(entry.options.get(CONF_ADAPTER, ADAPTER_AUTO))
    try:
        adapters = await broadcaster.async_get_adapters()
    except BroadcastError as err:
        raise ConfigEntryNotReady(
            translation_domain=err.translation_domain,
            translation_key=err.translation_key,
            translation_placeholders=err.translation_placeholders,
        ) from err

    for adapter in adapters:
        _LOGGER.debug("Found BlueZ adapter %s: %s", adapter.label, adapter.as_dict())
    if not any(adapter.advertising for adapter in adapters):
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN, translation_key="no_adapter"
        )
    default = broadcaster.default_adapter
    if default != ADAPTER_AUTO and not any(a.matches(default) for a in adapters):
        _LOGGER.warning(
            "Default adapter %s not found; signals using it will fail until it "
            "returns or another adapter is chosen in the integration options",
            default,
        )

    entry.runtime_data = broadcaster
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: BluetoothEnergyTransmitterConfigEntry
) -> bool:
    """Unload buttons and stop any advertisement still on air."""
    if unloaded := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        await entry.runtime_data.async_close()
    return unloaded


async def _async_update_listener(
    hass: HomeAssistant, entry: BluetoothEnergyTransmitterConfigEntry
) -> None:
    """Reload when options change or signals are added, edited or removed."""
    hass.config_entries.async_schedule_reload(entry.entry_id)
