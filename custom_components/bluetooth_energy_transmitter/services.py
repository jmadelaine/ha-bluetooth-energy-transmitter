"""The bluetooth_energy_transmitter.send action."""

from __future__ import annotations

from typing import TYPE_CHECKING

import voluptuous as vol

from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv

from .advertisement import ADVERTISEMENT_FIELDS, Advertisement
from .const import (
    CONF_ADAPTER,
    CONF_DURATION,
    DEFAULT_DURATION,
    DOMAIN,
    MAX_DURATION,
    MIN_DURATION,
    SERVICE_SEND,
)

if TYPE_CHECKING:
    from . import BluetoothEnergyTransmitterConfigEntry

SEND_SCHEMA = vol.Schema(
    {
        **ADVERTISEMENT_FIELDS,
        vol.Optional(CONF_DURATION, default=DEFAULT_DURATION): vol.All(
            vol.Coerce(float), vol.Range(min=MIN_DURATION, max=MAX_DURATION)
        ),
        vol.Optional(CONF_ADAPTER): cv.string,
    }
)


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register integration actions."""

    async def async_send(call: ServiceCall) -> None:
        entries: list[BluetoothEnergyTransmitterConfigEntry] = (
            hass.config_entries.async_loaded_entries(DOMAIN)
        )
        if not entries:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="not_loaded"
            )
        try:
            advertisement = Advertisement.from_config(call.data)
        except vol.Invalid as err:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="invalid_advertisement",
                translation_placeholders={"error": str(err)},
            ) from err
        await entries[0].runtime_data.async_broadcast(
            advertisement, call.data[CONF_DURATION], call.data.get(CONF_ADAPTER)
        )

    hass.services.async_register(DOMAIN, SERVICE_SEND, async_send, schema=SEND_SCHEMA)
