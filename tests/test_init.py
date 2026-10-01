"""Tests for setup, the button platform and the send action."""

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.components.button import DOMAIN as BUTTON_DOMAIN, SERVICE_PRESS
from homeassistant.config_entries import ConfigEntryState, ConfigSubentryData
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import device_registry as dr

from .conftest import ADAPTER_PATH, FakeBlueZ, adapter_objects
from custom_components.bluetooth_energy_transmitter.const import (
    DOMAIN,
    SUBENTRY_TYPE_SIGNAL,
)
from custom_components.bluetooth_energy_transmitter.diagnostics import (
    async_get_config_entry_diagnostics,
)

HID_UUID = "00001812-0000-1000-8000-00805f9b34fb"

XGIMI_DATA = {
    "advertisement_type": "peripheral",
    "duration": 4.0,
    "adapter": "default",
    "manufacturer_id": 70,
    "payload": "aabbcc",
    "local_name": "Bluetooth 4.0 RC",
    "service_uuids": [HID_UUID],
    "appearance": 961,
}


@pytest.fixture
async def entry(hass: HomeAssistant, bluez: FakeBlueZ) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        options={"adapter": "auto"},
        subentries_data=[
            ConfigSubentryData(
                data=XGIMI_DATA,
                subentry_type=SUBENTRY_TYPE_SIGNAL,
                title="Projector power on",
                unique_id=None,
            )
        ],
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_button_press_broadcasts(
    hass: HomeAssistant, entry: MockConfigEntry, bluez: FakeBlueZ
) -> None:
    [subentry_id] = entry.subentries
    device = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, subentry_id)})
    assert device is not None
    assert device.name == "Projector power on"

    await hass.services.async_call(
        BUTTON_DOMAIN,
        SERVICE_PRESS,
        {ATTR_ENTITY_ID: "button.projector_power_on"},
        blocking=True,
    )
    [(adapter_path, properties)] = bluez.registrations
    assert adapter_path == ADAPTER_PATH
    assert properties["ManufacturerData"] == ("a{qv}", {70: b"\xaa\xbb\xcc"})
    assert properties["LocalName"] == ("s", "Bluetooth 4.0 RC")

    # Unloading stops the advertisement that is still on air.
    assert bluez.registered
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert not bluez.registered


async def test_send_action(
    hass: HomeAssistant, entry: MockConfigEntry, bluez: FakeBlueZ
) -> None:
    await hass.services.async_call(
        DOMAIN,
        "send",
        {
            "manufacturer_id": "0x004c",
            "payload": "02 15",
            "advertisement_type": "broadcast",
            "duration": 1,
            "adapter": "hci0",
        },
        blocking=True,
    )
    [(_, properties)] = bluez.registrations
    assert properties == {
        "Type": ("s", "broadcast"),
        "ManufacturerData": ("a{qv}", {0x4C: b"\x02\x15"}),
    }
    await hass.config_entries.async_unload(entry.entry_id)


async def test_send_action_errors(
    hass: HomeAssistant, entry: MockConfigEntry, bluez: FakeBlueZ
) -> None:
    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            DOMAIN, "send", {"min_interval": 200, "max_interval": 100}, blocking=True
        )
    assert err.value.translation_key == "invalid_advertisement"

    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call(
            DOMAIN,
            "send",
            {"manufacturer_id": 70, "payload": "01", "adapter": "hci7"},
            blocking=True,
        )
    assert err.value.translation_key == "adapter_not_found"
    assert "hci7" in str(err.value)

    await hass.config_entries.async_unload(entry.entry_id)
    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            DOMAIN, "send", {"manufacturer_id": 70, "payload": "01"}, blocking=True
        )
    assert err.value.translation_key == "not_loaded"


@pytest.mark.parametrize(
    "setup",
    [
        lambda bluez: setattr(bluez, "connect_error", FileNotFoundError("no bus")),
        lambda bluez: setattr(bluez, "objects", adapter_objects(advertising=False)),
    ],
)
async def test_setup_retries_without_bluez(
    hass: HomeAssistant, bluez: FakeBlueZ, setup
) -> None:
    setup(bluez)
    entry = MockConfigEntry(domain=DOMAIN, options={"adapter": "auto"})
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_diagnostics_redacts_payload(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    diagnostics = await async_get_config_entry_diagnostics(hass, entry)
    assert diagnostics["adapters"][0]["supported_instances"] == 5
    [signal] = diagnostics["signals"]
    assert signal["data"]["payload"] == "**REDACTED**"
    assert signal["data"]["manufacturer_id"] == 70
