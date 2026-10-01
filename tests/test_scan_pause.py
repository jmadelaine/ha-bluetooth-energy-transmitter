"""Tests for pausing Home Assistant's Bluetooth scanning while broadcasting."""

import asyncio
from collections.abc import Generator
from unittest.mock import patch

import pytest

from homeassistant.core import HomeAssistant

from .conftest import ADAPTER_ADDRESS, FakeBlueZ
from custom_components.bluetooth_energy_transmitter.advertisement import Advertisement
from custom_components.bluetooth_energy_transmitter.bluez import Broadcaster
from custom_components.bluetooth_energy_transmitter.scan_pause import ScanPauser

ADV = Advertisement.from_config({"manufacturer_id": 0x46, "payload": "aabb"})
OTHER = Advertisement.from_config({"manufacturer_id": 0x46, "payload": "ccdd"})


class FakeScanner:
    """The parts of habluetooth's HaScanner the pauser uses."""

    def __init__(self, events: list[str]) -> None:
        self.scanning = True
        self.events = events

    async def async_stop(self) -> None:
        self.scanning = False
        self.events.append("scan stopped")

    async def async_start(self) -> None:
        self.scanning = True
        self.events.append("scan started")


@pytest.fixture
def events() -> list[str]:
    return []


@pytest.fixture
def scanner(hass: HomeAssistant, events: list[str]) -> Generator[FakeScanner]:
    hass.config.components.add("bluetooth")
    with patch(
        "custom_components.bluetooth_energy_transmitter.scan_pause.SETTLE_DELAY", 0
    ):
        yield FakeScanner(events)


def _pause(hass: HomeAssistant, scanner: FakeScanner):
    """Return a scan pause hook whose lookup finds `scanner` for our adapter."""

    def lookup(_: HomeAssistant, source: str) -> FakeScanner | None:
        return scanner if source == ADAPTER_ADDRESS else None

    return ScanPauser(hass, lookup).async_pause


async def _until(condition) -> None:
    for _ in range(200):
        if condition():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition never became true")


async def test_scanning_paused_for_the_broadcast(
    hass: HomeAssistant, bluez: FakeBlueZ, scanner: FakeScanner, events: list[str]
) -> None:
    broadcaster = Broadcaster(scan_pause=_pause(hass, scanner))
    real_register = broadcaster._async_register

    async def register(*args, **kwargs) -> None:
        events.append("registered")
        await real_register(*args, **kwargs)

    with patch.object(broadcaster, "_async_register", register):
        await broadcaster.async_broadcast(ADV, 0.05)
        assert not scanner.scanning
        await _until(lambda: not broadcaster.active_count)

    assert events == ["scan stopped", "registered", "scan started"]
    assert scanner.scanning


async def test_overlapping_broadcasts_pause_once(
    hass: HomeAssistant, bluez: FakeBlueZ, scanner: FakeScanner, events: list[str]
) -> None:
    broadcaster = Broadcaster(scan_pause=_pause(hass, scanner))
    await broadcaster.async_broadcast(ADV, 0.1)
    await broadcaster.async_broadcast(OTHER, 0.3)
    await _until(lambda: broadcaster.active_count == 1)
    # The first broadcast ended, but the second still needs the radio.
    assert not scanner.scanning
    await _until(lambda: not broadcaster.active_count)
    assert events == ["scan stopped", "scan started"]


async def test_closing_resumes_scanning(
    hass: HomeAssistant, bluez: FakeBlueZ, scanner: FakeScanner
) -> None:
    broadcaster = Broadcaster(scan_pause=_pause(hass, scanner))
    await broadcaster.async_broadcast(ADV, 10)
    assert not scanner.scanning
    await broadcaster.async_close()
    assert scanner.scanning


async def test_failed_registration_resumes_scanning(
    hass: HomeAssistant, bluez: FakeBlueZ, scanner: FakeScanner, events: list[str]
) -> None:
    bluez.register_error = ("org.bluez.Error.Failed", "Failed")
    broadcaster = Broadcaster(scan_pause=_pause(hass, scanner))
    with pytest.raises(Exception):  # noqa: B017
        await broadcaster.async_broadcast(ADV, 1)
    assert events == ["scan stopped", "scan started"]


async def test_scanner_already_stopped_is_left_alone(
    hass: HomeAssistant, bluez: FakeBlueZ, scanner: FakeScanner, events: list[str]
) -> None:
    scanner.scanning = False
    broadcaster = Broadcaster(scan_pause=_pause(hass, scanner))
    await broadcaster.async_broadcast(ADV, 0.05)
    await _until(lambda: not broadcaster.active_count)
    assert events == []


async def test_without_bluetooth_integration(
    hass: HomeAssistant, bluez: FakeBlueZ
) -> None:
    # Bluetooth isn't loaded, so the real lookup is never imported or called.
    broadcaster = Broadcaster(scan_pause=ScanPauser(hass).async_pause)
    await broadcaster.async_broadcast(ADV, 0.05)
    await _until(lambda: not broadcaster.active_count)
    assert bluez.registrations


async def test_option_off_leaves_scanning_alone(
    hass: HomeAssistant, bluez: FakeBlueZ, scanner: FakeScanner, events: list[str]
) -> None:
    broadcaster = Broadcaster()
    await broadcaster.async_broadcast(ADV, 0.05)
    await _until(lambda: not broadcaster.active_count)
    assert events == []
