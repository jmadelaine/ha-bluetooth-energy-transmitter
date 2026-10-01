"""Tests for the config, options and signal subentry flows."""

from typing import Any

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.config_entries import (
    SOURCE_RECONFIGURE,
    SOURCE_USER,
    ConfigSubentryData,
)
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from .conftest import ADAPTER_ADDRESS, FakeBlueZ
from custom_components.bluetooth_energy_transmitter.const import (
    DOMAIN,
    SUBENTRY_TYPE_SIGNAL,
)

HID_UUID = "00001812-0000-1000-8000-00805f9b34fb"

XGIMI_FORM = {
    "name": "Projector power on",
    "manufacturer_id": "70",
    "payload": "AA BB CC",
    "local_name": "Bluetooth 4.0 RC",
    "service_uuids": ["1812"],
    "appearance": 961.0,
    "advertisement_type": "peripheral",
    "duration": 4.0,
    "adapter": "default",
    "advanced": {"include_tx_power": False, "discoverable": False},
}

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


async def test_user_flow(hass: HomeAssistant, bluez: FakeBlueZ) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    options = result["data_schema"].schema["adapter"].config["options"]
    assert [option["value"] for option in options] == ["auto", ADAPTER_ADDRESS]

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"adapter": ADAPTER_ADDRESS}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Bluetooth Energy Transmitter"
    assert result["options"] == {"adapter": ADAPTER_ADDRESS}


async def test_user_flow_without_adapters(
    hass: HomeAssistant, bluez: FakeBlueZ
) -> None:
    bluez.connect_error = FileNotFoundError("no system bus")
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_adapters"


async def test_single_instance(hass: HomeAssistant, bluez: FakeBlueZ) -> None:
    MockConfigEntry(domain=DOMAIN).add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "single_instance_allowed"


async def test_options_flow(hass: HomeAssistant, bluez: FakeBlueZ) -> None:
    entry = MockConfigEntry(domain=DOMAIN, options={"adapter": "auto"})
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"adapter": "hci0"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.options == {"adapter": "hci0"}
    assert entry.runtime_data.default_adapter == "hci0"


async def _start_signal_flow(
    hass: HomeAssistant, entry: MockConfigEntry, choice: str = "manual"
) -> dict[str, Any]:
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TYPE_SIGNAL), context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.MENU
    assert result["menu_options"] == ["paste", "manual"]
    return await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"next_step_id": choice}
    )


@pytest.fixture
async def entry(hass: HomeAssistant, bluez: FakeBlueZ) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, options={"adapter": "auto"})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    return entry


async def test_add_signal(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    result = await _start_signal_flow(hass, entry)
    assert result["type"] is FlowResultType.FORM

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], XGIMI_FORM
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()

    [subentry] = entry.subentries.values()
    assert subentry.title == "Projector power on"
    assert dict(subentry.data) == XGIMI_DATA
    assert hass.states.get("button.projector_power_on") is not None


@pytest.mark.parametrize(
    ("changes", "errors"),
    [
        ({"manufacturer_id": "0046"}, {"manufacturer_id": "invalid_manufacturer_id"}),
        ({"payload": "abc"}, {"payload": "invalid_hex"}),
        ({"service_uuids": ["nope"]}, {"service_uuids": "invalid_uuid"}),
        ({"payload": ""}, {"payload": "manufacturer_data_incomplete"}),
        ({"advanced": {"service_data": "fe95 01"}}, {"base": "invalid_service_data"}),
        ({"advanced": {"data": "999=01"}}, {"base": "invalid_data"}),
        (
            {"advanced": {"min_interval": 200.0, "max_interval": 100.0}},
            {"base": "invalid_interval"},
        ),
    ],
)
async def test_add_signal_errors(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    changes: dict[str, Any],
    errors: dict[str, str],
) -> None:
    result = await _start_signal_flow(hass, entry)
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {**XGIMI_FORM, **changes}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == errors


async def test_add_signal_advanced(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    result = await _start_signal_flow(hass, entry)
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            "name": "Thermometer",
            "advertisement_type": "broadcast",
            "duration": 2.0,
            "adapter": "hci0",
            "advanced": {
                "service_data": "fe95=3020\n181a=01",
                "include_tx_power": True,
                "discoverable": False,
                "tx_power": 5.0,
                "data": "0x16=0d18",
            },
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {
        "advertisement_type": "broadcast",
        "duration": 2.0,
        "adapter": "hci0",
        "service_data": {
            "0000fe95-0000-1000-8000-00805f9b34fb": "3020",
            "0000181a-0000-1000-8000-00805f9b34fb": "01",
        },
        "include_tx_power": True,
        "tx_power": 5,
        "data": {0x16: "0d18"},
    }


def _suggested(schema: dict) -> dict[str, Any]:
    return {
        str(key): key.description["suggested_value"]
        for key in schema
        if key.description and "suggested_value" in key.description
    }


async def test_reconfigure_signal(hass: HomeAssistant, bluez: FakeBlueZ) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        options={"adapter": "auto"},
        subentries_data=[
            ConfigSubentryData(
                data={**XGIMI_DATA, "data": {"22": "0d18"}},
                subentry_type=SUBENTRY_TYPE_SIGNAL,
                title="Projector power on",
                unique_id=None,
            )
        ],
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    [subentry_id] = entry.subentries

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TYPE_SIGNAL),
        context={"source": SOURCE_RECONFIGURE, "subentry_id": subentry_id},
    )
    assert result["type"] is FlowResultType.FORM
    schema = result["data_schema"].schema
    suggested = _suggested(schema)
    assert suggested["manufacturer_id"] == "0x0046"
    assert suggested["name"] == "Projector power on"
    assert _suggested(schema["advanced"].schema.schema)["data"] == "0x16=0d18"

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {**XGIMI_FORM, "name": "Projector on", "payload": "dd"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    await hass.async_block_till_done()

    subentry = entry.subentries[subentry_id]
    assert subentry.title == "Projector on"
    assert subentry.data["payload"] == "dd"


# Same shape as a real XGIMI remote advertisement; token and address are made up.
MONITOR_JSON = (
    '{"name":"XGIMI RC","address":"00:1A:7D:00:00:02","rssi":-58,'
    '"manufacturer_data":{"70":"00112233445566778899aabbccddeeff"},"service_data":{},'
    '"service_uuids":["00001812-0000-1000-8000-00805f9b34fb",'
    '"00004912-0000-1000-8000-00805f9b34fb"],"source":"00:1A:7D:00:00:01",'
    '"connectable":true,"time":1790876100.28,"tx_power":null,'
    '"raw":"020105030312180319c10313ff460000112233445566778899aabbccddeeff"}'
)


async def test_add_signal_by_pasting(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    result = await _start_signal_flow(hass, entry, "paste")
    assert result["step_id"] == "paste"

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"advertisement": "not an advertisement"}
    )
    assert result["errors"] == {"base": "invalid_raw"}

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"advertisement": MONITOR_JSON}
    )
    # The regular form, pre-filled for review.
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "manual"
    suggested = _suggested(result["data_schema"].schema)
    assert suggested["name"] == "XGIMI RC"
    assert suggested["manufacturer_id"] == "0x0046"
    assert suggested["appearance"] == 961

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            "name": "Projector power",
            "manufacturer_id": suggested["manufacturer_id"],
            "payload": suggested["payload"],
            "local_name": suggested["local_name"],
            "service_uuids": suggested["service_uuids"],
            "appearance": suggested["appearance"],
            "advertisement_type": suggested["advertisement_type"],
            "duration": suggested["duration"],
            "adapter": suggested["adapter"],
            "advanced": {"include_tx_power": False, "discoverable": False},
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Projector power"
    assert result["data"] == {
        "advertisement_type": "peripheral",
        "duration": 4.0,
        "adapter": "default",
        "manufacturer_id": 70,
        "payload": "00112233445566778899aabbccddeeff",
        "local_name": "XGIMI RC",
        "service_uuids": [HID_UUID],
        "appearance": 961,
    }
