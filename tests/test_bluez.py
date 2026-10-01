"""Tests for the BlueZ broadcaster against a fake system bus."""

import asyncio
from collections.abc import AsyncGenerator

import pytest

from .conftest import ADAPTER_ADDRESS, ADAPTER_PATH, FakeBlueZ, adapter_objects
from custom_components.bluetooth_energy_transmitter.advertisement import Advertisement
from custom_components.bluetooth_energy_transmitter.bluez import (
    Broadcaster,
    BroadcastError,
)

HID_UUID = "00001812-0000-1000-8000-00805f9b34fb"

XGIMI = Advertisement.from_config(
    {
        "manufacturer_id": 0x46,
        "payload": "aabbcc",
        "local_name": "Bluetooth 4.0 RC",
        "service_uuids": ["1812"],
        "appearance": 961,
    }
)


@pytest.fixture
async def broadcaster(bluez: FakeBlueZ) -> AsyncGenerator[Broadcaster]:
    broadcaster = Broadcaster()
    yield broadcaster
    await broadcaster.async_close()


async def _until(condition) -> None:
    for _ in range(100):
        if condition():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition never became true")


async def test_broadcast_registers_only_set_fields(
    bluez: FakeBlueZ, broadcaster: Broadcaster
) -> None:
    adapter = await broadcaster.async_broadcast(XGIMI, 0.05)

    assert adapter.path == ADAPTER_PATH
    [(adapter_path, properties)] = bluez.registrations
    assert adapter_path == ADAPTER_PATH
    assert properties == {
        "Type": ("s", "peripheral"),
        "LocalName": ("s", "Bluetooth 4.0 RC"),
        "ServiceUUIDs": ("as", [HID_UUID]),
        # The company ID is a key; BlueZ adds it to the packet itself.
        "ManufacturerData": ("a{qv}", {0x46: b"\xaa\xbb\xcc"}),
        "Appearance": ("q", 961),
    }

    # Still on air after the call returns, then unregistered and cleaned up.
    assert bluez.registered
    await _until(lambda: not broadcaster.active_count)
    assert not bluez.registered
    assert len(bluez.unregistrations) == 1
    assert not bluez.buses[-1].connected
    assert not bluez.buses[-1].exported


async def test_every_field_has_a_valid_signature(
    bluez: FakeBlueZ, broadcaster: Broadcaster
) -> None:
    adv = Advertisement.from_config(
        {
            "advertisement_type": "broadcast",
            "manufacturer_id": 0xFFFF,
            "payload": "",
            "local_name": "x",
            "service_uuids": ["180f", "12345678-1234-5678-1234-567812345678"],
            "service_data": {"fe95": "01"},
            "solicit_uuids": ["1812"],
            "appearance": 0,
            "include_tx_power": True,
            "tx_power": -20,
            "discoverable": True,
            "min_interval": 100,
            "max_interval": 150,
            "data": {0x16: "0d18"},
        }
    )
    await broadcaster.async_broadcast(adv, 0.01)
    [(_, properties)] = bluez.registrations
    assert properties["Appearance"] == ("q", 0)
    assert properties["Includes"] == ("as", ["tx-power"])
    assert properties["TxPower"] == ("n", -20)
    assert properties["Data"] == ("a{yv}", {0x16: b"\x0d\x18"})
    assert properties["MinInterval"] == ("u", 100)
    assert len(properties) == 13


async def test_identical_broadcasts_share_one_registration(
    bluez: FakeBlueZ, broadcaster: Broadcaster
) -> None:
    await asyncio.gather(
        broadcaster.async_broadcast(XGIMI, 0.1),
        broadcaster.async_broadcast(XGIMI, 0.1),
    )
    await broadcaster.async_broadcast(XGIMI, 0.1)
    assert len(bluez.registrations) == 1

    other = Advertisement.from_config({"manufacturer_id": 0x46, "payload": "01"})
    await broadcaster.async_broadcast(other, 0.1)
    assert len(bluez.registrations) == 2
    await broadcaster.async_close()


@pytest.mark.parametrize(
    "requested", ["hci1", "/org/bluez/hci1", ADAPTER_ADDRESS.lower()]
)
async def test_select_adapter_by_name_path_or_mac(
    bluez: FakeBlueZ, broadcaster: Broadcaster, requested: str
) -> None:
    bluez.objects = {
        **adapter_objects("/org/bluez/hci0", "00:00:00:00:00:01"),
        **adapter_objects("/org/bluez/hci1", ADAPTER_ADDRESS),
    }
    adapter = await broadcaster.async_broadcast(XGIMI, 0.01, requested)
    assert adapter.path == "/org/bluez/hci1"


async def test_auto_skips_full_and_unpowered_adapters(
    bluez: FakeBlueZ, broadcaster: Broadcaster
) -> None:
    bluez.objects = {
        **adapter_objects("/org/bluez/hci0", "00:00:00:00:00:01", powered=False),
        **adapter_objects("/org/bluez/hci1", "00:00:00:00:00:02", active=5),
        **adapter_objects("/org/bluez/hci2", "00:00:00:00:00:03"),
    }
    adapter = await broadcaster.async_broadcast(XGIMI, 0.01)
    assert adapter.path == "/org/bluez/hci2"


@pytest.mark.parametrize(
    ("objects", "requested", "key"),
    [
        ({}, "auto", "no_adapter"),
        (adapter_objects(advertising=False), "auto", "no_adapter"),
        (adapter_objects(advertising=False), "hci0", "adapter_no_advertising"),
        (adapter_objects(powered=False), "auto", "adapter_not_powered"),
        (adapter_objects(active=5), "auto", "no_free_instance"),
        (adapter_objects(), "hci9", "adapter_not_found"),
    ],
)
async def test_adapter_selection_errors(
    bluez: FakeBlueZ,
    broadcaster: Broadcaster,
    objects: dict,
    requested: str,
    key: str,
) -> None:
    bluez.objects = objects
    with pytest.raises(BroadcastError) as err:
        await broadcaster.async_broadcast(XGIMI, 0.01, requested)
    assert err.value.translation_key == key
    assert not bluez.registrations


@pytest.mark.parametrize(
    ("error", "key"),
    [
        (
            ("org.bluez.Error.InvalidLength", "Advertising data too long."),
            "advertisement_too_long",
        ),
        (
            ("org.bluez.Error.InvalidArguments", "Invalid arguments"),
            "advertisement_rejected",
        ),
        (
            ("org.bluez.Error.Failed", "Maximum advertisements reached"),
            "no_free_instance",
        ),
        (
            ("org.bluez.Error.Failed", "Failed to register advertisement"),
            "registration_failed",
        ),
    ],
)
async def test_registration_errors(
    bluez: FakeBlueZ, broadcaster: Broadcaster, error: tuple[str, str], key: str
) -> None:
    bluez.register_error = error
    with pytest.raises(BroadcastError) as err:
        await broadcaster.async_broadcast(XGIMI, 0.01)
    assert err.value.translation_key == key
    assert broadcaster.active_count == 0
    assert not bluez.buses[-1].connected
    assert not bluez.buses[-1].exported


async def test_dbus_unavailable(bluez: FakeBlueZ, broadcaster: Broadcaster) -> None:
    bluez.connect_error = FileNotFoundError("/run/dbus/system_bus_socket")
    with pytest.raises(BroadcastError) as err:
        await broadcaster.async_broadcast(XGIMI, 0.01)
    assert err.value.translation_key == "dbus_unavailable"


async def test_close_stops_early_and_unregisters(
    bluez: FakeBlueZ, broadcaster: Broadcaster
) -> None:
    await broadcaster.async_broadcast(XGIMI, 10)
    assert bluez.registered
    await asyncio.wait_for(broadcaster.async_close(), 1)
    assert not bluez.registered
    with pytest.raises(BroadcastError) as err:
        await broadcaster.async_broadcast(XGIMI, 10)
    assert err.value.translation_key == "broadcaster_closed"


async def test_release_by_bluez_skips_unregister(
    bluez: FakeBlueZ, broadcaster: Broadcaster
) -> None:
    await broadcaster.async_broadcast(XGIMI, 10)
    [interface] = bluez.buses[-1].exported.values()
    interface.release()
    await _until(lambda: not broadcaster.active_count)
    assert not bluez.unregistrations


async def test_cancelled_caller_does_not_cancel_broadcast(
    bluez: FakeBlueZ, broadcaster: Broadcaster
) -> None:
    bluez.register_gate = asyncio.Event()
    caller = asyncio.create_task(broadcaster.async_broadcast(XGIMI, 0.05))
    await asyncio.sleep(0.01)
    caller.cancel()
    bluez.register_gate.set()
    await _until(lambda: bluez.registrations)
    await _until(lambda: not broadcaster.active_count)
    assert bluez.unregistrations


async def test_warns_when_over_legacy_length(
    bluez: FakeBlueZ, broadcaster: Broadcaster, caplog: pytest.LogCaptureFixture
) -> None:
    long = Advertisement.from_config({"manufacturer_id": 0x46, "payload": "00" * 30})
    await broadcaster.async_broadcast(long, 0.01)
    assert "extended advertising" in caplog.text
    assert bluez.registrations

    caplog.clear()
    await broadcaster.async_broadcast(XGIMI, 0.01)
    assert "extended advertising" not in caplog.text
