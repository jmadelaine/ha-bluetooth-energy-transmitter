"""Tests for input normalization and the advertisement model."""

import json

import pytest
import voluptuous as vol

from custom_components.bluetooth_energy_transmitter.advertisement import (
    Advertisement,
    ad_data_map,
    normalize_hex,
    normalize_uuid,
    service_data_map,
    uint16,
    uuid_list,
)

HID_UUID = "00001812-0000-1000-8000-00805f9b34fb"


@pytest.mark.parametrize(
    "value",
    [
        "0a1b2c",
        "0A1B2C",
        "0x0a1b2c",
        "0a 1b 2c",
        "0a:1b:2c",
        "0x0a 0x1b 0x2c",
        " 0a-1b-2c ",
    ],
)
def test_normalize_hex(value: str) -> None:
    assert normalize_hex(value) == "0a1b2c"


@pytest.mark.parametrize("value", ["0a1", "zz", 12, None])
def test_normalize_hex_invalid(value: object) -> None:
    with pytest.raises(vol.Invalid):
        normalize_hex(value)


def test_normalize_hex_empty() -> None:
    assert normalize_hex("") == ""


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (70, 0x46),
        ("70", 0x46),
        ("0x0046", 0x46),
        ("0X46", 0x46),
        (961.0, 961),
        ("0", 0),
    ],
)
def test_uint16(value: object, expected: int) -> None:
    assert uint16(value) == expected


@pytest.mark.parametrize("value", ["0046", "46h", "004c", True, 1.5, 0x10000, -1])
def test_uint16_invalid(value: object) -> None:
    with pytest.raises(vol.Invalid):
        uint16(value)


@pytest.mark.parametrize(
    "value",
    [
        "1812",
        "0x1812",
        "00001812",
        HID_UUID,
        HID_UUID.upper(),
        HID_UUID.replace("-", ""),
    ],
)
def test_normalize_uuid(value: str) -> None:
    assert normalize_uuid(value) == HID_UUID


def test_uuid_list() -> None:
    assert uuid_list("1812, 180f 1812") == [
        HID_UUID,
        "0000180f-0000-1000-8000-00805f9b34fb",
    ]
    assert uuid_list(["1812", ""]) == [HID_UUID]
    with pytest.raises(vol.Invalid):
        uuid_list(["nope"])


def test_mappings() -> None:
    assert service_data_map("fe95=30 20\n180f = 64") == {
        "0000fe95-0000-1000-8000-00805f9b34fb": "3020",
        "0000180f-0000-1000-8000-00805f9b34fb": "64",
    }
    assert service_data_map({"fe95": "3020"}) == {
        "0000fe95-0000-1000-8000-00805f9b34fb": "3020"
    }
    assert ad_data_map("0x16=0d18ff; 255=01") == {0x16: "0d18ff", 255: "01"}
    with pytest.raises(vol.Invalid):
        ad_data_map("256=01")
    with pytest.raises(vol.Invalid):
        service_data_map("fe95 3020")


def test_from_config_xgimi() -> None:
    adv = Advertisement.from_config(
        {
            "manufacturer_id": "0x0046",
            "payload": "AA BB CC",
            "local_name": "Bluetooth 4.0 RC",
            "service_uuids": ["1812"],
            "appearance": 961,
        }
    )
    assert adv == Advertisement(
        advertisement_type="peripheral",
        manufacturer_id=0x46,
        manufacturer_payload=b"\xaa\xbb\xcc",
        local_name="Bluetooth 4.0 RC",
        service_uuids=(HID_UUID,),
        appearance=961,
    )
    # flags 3 + manufacturer (2 + 2 + 3) + one 16-bit UUID (2 + 2) + appearance 4;
    # the local name goes in the scan response.
    assert adv.estimated_length == 18
    assert "manufacturer=0x0046:aabbcc" in adv.describe()


def test_from_config_survives_json_round_trip() -> None:
    config = {
        "manufacturer_id": 70,
        "payload": "aabb",
        "data": ad_data_map("0x16=0d18"),
        "service_data": service_data_map("fe95=01"),
        "min_interval": 100,
        "max_interval": 200,
    }
    assert Advertisement.from_config(json.loads(json.dumps(config))) == (
        Advertisement.from_config(config)
    )


def test_from_config_rejects_incomplete_manufacturer_data() -> None:
    with pytest.raises(vol.Invalid):
        Advertisement.from_config({"manufacturer_id": 70})


def test_from_config_rejects_inverted_interval() -> None:
    with pytest.raises(vol.Invalid):
        Advertisement.from_config({"min_interval": 200, "max_interval": 100})


def test_advertisements_are_hashable() -> None:
    first = Advertisement.from_config({"manufacturer_id": 70, "payload": "aa"})
    second = Advertisement.from_config({"manufacturer_id": "0x46", "payload": "AA"})
    assert {first, second} == {first}
