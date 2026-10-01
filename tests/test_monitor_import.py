"""Tests for importing advertisements copied from the Advertisement Monitor."""

import json

import pytest

from .test_config_flow import MONITOR_JSON
from custom_components.bluetooth_energy_transmitter.advertisement import Advertisement
from custom_components.bluetooth_energy_transmitter.monitor_import import (
    AdvertisementImportError,
    parse_monitor_advertisement,
)

HID_UUID = "00001812-0000-1000-8000-00805f9b34fb"
TOKEN = "00112233445566778899aabbccddeeff"


def test_xgimi_remote_replays_the_raw_packet() -> None:
    name, config = parse_monitor_advertisement(MONITOR_JSON)
    assert name == "XGIMI RC"
    # The second UUID is only in the scan response the monitor merged in, so it
    # is left out; the name is kept because BlueZ sends it in the scan response.
    assert config == {
        "advertisement_type": "peripheral",
        "manufacturer_id": 0x46,
        "payload": TOKEN,
        "service_uuids": [HID_UUID],
        "appearance": 961,
        "local_name": "XGIMI RC",
    }
    # Fits a legacy packet exactly, like the original.
    assert Advertisement.from_config(config).estimated_length == 31


def test_without_raw_uses_decoded_fields() -> None:
    entry = json.loads(MONITOR_JSON) | {"raw": None, "connectable": False}
    name, config = parse_monitor_advertisement(json.dumps(entry))
    assert name == "XGIMI RC"
    assert config == {
        "advertisement_type": "broadcast",
        "manufacturer_id": 0x46,
        "payload": TOKEN,
        "service_uuids": [HID_UUID, "00004912-0000-1000-8000-00805f9b34fb"],
        "local_name": "XGIMI RC",
    }


def test_address_as_name_is_not_a_local_name() -> None:
    entry = json.loads(MONITOR_JSON) | {"raw": None, "name": "00:1A:7D:00:00:02"}
    name, config = parse_monitor_advertisement(json.dumps(entry))
    assert name is None
    assert "local_name" not in config


def test_bare_raw_hex() -> None:
    raw = (
        "02 01 06"  # flags, dropped
        " 05 09 4c 61 6d 70"  # complete name "Lamp"
        " 02 0a 00"  # TX power level
        " 05 16 95 fe 30 20"  # service data fe95 = 3020
        " 11 07 "
        + "".join(f"{b:02x}" for b in bytes(range(16)))  # 128-bit UUID
        + " 03 1b 01 02"  # unknown AD type, passed through
        + " 00 00"  # zero padding
    )
    name, config = parse_monitor_advertisement(raw)
    assert name == "Lamp"
    assert config == {
        "advertisement_type": "peripheral",
        "local_name": "Lamp",
        "include_tx_power": True,
        "service_data": {"0000fe95-0000-1000-8000-00805f9b34fb": "3020"},
        "service_uuids": ["0f0e0d0c-0b0a-0908-0706-050403020100"],
        "data": {0x1B: "0102"},
    }
    Advertisement.from_config(config)


@pytest.mark.parametrize(
    ("text", "key"),
    [
        ("{not json", "invalid_monitor_json"),
        ("[1, 2]", "invalid_raw"),
        ('{"manufacturer_data": {"x": "01"}}', "invalid_monitor_json"),
        ("05ff4600", "invalid_raw"),  # truncated structure
        ("0303121", "invalid_raw"),  # odd hex
        ("020106", "empty_advertisement"),  # flags only
        ("04ff460001 04ff4c0002", "multiple_manufacturers"),
    ],
)
def test_errors(text: str, key: str) -> None:
    with pytest.raises(AdvertisementImportError) as err:
        parse_monitor_advertisement(text)
    assert err.value.key == key
