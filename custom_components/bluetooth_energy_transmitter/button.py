"""A button per saved signal."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigSubentry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import BluetoothEnergyTransmitterConfigEntry
from .advertisement import Advertisement
from .const import CONF_ADAPTER, CONF_DURATION, DOMAIN, SUBENTRY_TYPE_SIGNAL


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BluetoothEnergyTransmitterConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add one button for each saved signal."""
    for subentry in entry.subentries.values():
        if subentry.subentry_type == SUBENTRY_TYPE_SIGNAL:
            async_add_entities(
                [SignalButton(entry, subentry)],
                config_subentry_id=subentry.subentry_id,
            )


class SignalButton(ButtonEntity):
    """Broadcast a saved signal when pressed."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_translation_key = "signal"

    def __init__(
        self, entry: BluetoothEnergyTransmitterConfigEntry, subentry: ConfigSubentry
    ) -> None:
        """Initialize from the saved signal."""
        self._broadcaster = entry.runtime_data
        self._advertisement = Advertisement.from_config(subentry.data)
        self._duration: float = subentry.data[CONF_DURATION]
        self._adapter: str | None = subentry.data.get(CONF_ADAPTER)
        self._attr_unique_id = subentry.subentry_id
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, subentry.subentry_id)},
            name=subentry.title,
            entry_type=DeviceEntryType.SERVICE,
            model="BLE advertisement",
        )

    async def async_press(self) -> None:
        """Broadcast the signal; returns once BlueZ accepted it."""
        await self._broadcaster.async_broadcast(
            self._advertisement, self._duration, self._adapter
        )
