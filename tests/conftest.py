"""Fixtures: a fake BlueZ on a fake system bus."""

from __future__ import annotations

import asyncio
from collections.abc import Generator
from dataclasses import dataclass, field
import io
from typing import Any
from unittest.mock import patch

from dbus_fast import Message, MessageType, Variant
from dbus_fast._private.unmarshaller import Unmarshaller
from dbus_fast.service import ServiceInterface
import pytest

ADAPTER_PATH = "/org/bluez/hci0"
ADAPTER_ADDRESS = "00:1A:7D:00:00:01"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Load integrations from custom_components."""


def adapter_objects(
    path: str = ADAPTER_PATH,
    address: str = ADAPTER_ADDRESS,
    *,
    powered: bool = True,
    advertising: bool = True,
    supported: int = 5,
    active: int = 0,
) -> dict[str, dict[str, Any]]:
    """Return GetManagedObjects interfaces for one adapter."""
    interfaces: dict[str, dict[str, Any]] = {
        "org.bluez.Adapter1": {
            "Address": Variant("s", address),
            "Alias": Variant("s", "homeassistant"),
            "Name": Variant("s", "homeassistant"),
            "Powered": Variant("b", powered),
        }
    }
    if advertising:
        interfaces["org.bluez.LEAdvertisingManager1"] = {
            "SupportedInstances": Variant("y", supported),
            "ActiveInstances": Variant("y", active),
            "SupportedIncludes": Variant(
                "as", ["tx-power", "appearance", "local-name"]
            ),
            "SupportedSecondaryChannels": Variant("as", ["1M", "2M", "Coded"]),
            "SupportedFeatures": Variant("as", ["CanSetTxPower", "HardwareOffload"]),
            "SupportedCapabilities": Variant(
                "a{sv}",
                {
                    "MaxAdvLen": Variant("y", 251),
                    "MaxScnRspLen": Variant("y", 251),
                    "MinTxPower": Variant("n", -34),
                    "MaxTxPower": Variant("n", 7),
                },
            ),
        }
    return {path: interfaces}


@dataclass
class FakeBlueZ:
    """State shared by every FakeBus connection."""

    objects: dict[str, dict[str, Any]] = field(default_factory=adapter_objects)
    connect_error: Exception | None = None
    register_error: tuple[str, str] | None = None
    # path -> {property: value} for advertisements currently registered
    registered: dict[str, dict[str, Any]] = field(default_factory=dict)
    registrations: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    unregistrations: list[str] = field(default_factory=list)
    buses: list[FakeBus] = field(default_factory=list)
    register_gate: asyncio.Event | None = None

    def factory(self) -> FakeBus:
        bus = FakeBus(self)
        self.buses.append(bus)
        return bus


class FakeBus:
    """Just enough of dbus_fast.aio.MessageBus for the broadcaster."""

    def __init__(self, bluez: FakeBlueZ) -> None:
        self._bluez = bluez
        self.connected = False
        self.exported: dict[str, ServiceInterface] = {}
        self._disconnected = asyncio.Event()

    async def connect(self) -> FakeBus:
        if self._bluez.connect_error is not None:
            raise self._bluez.connect_error
        self.connected = True
        return self

    def export(self, path: str, interface: ServiceInterface) -> None:
        self.exported[path] = interface

    def unexport(self, path: str, interface: ServiceInterface) -> None:
        del self.exported[path]

    def disconnect(self) -> None:
        self.connected = False
        self._disconnected.set()

    async def wait_for_disconnect(self) -> None:
        await self._disconnected.wait()

    async def call(self, message: Message) -> Message:
        bluez = self._bluez
        if message.member == "GetManagedObjects":
            return _reply("a{oa{sa{sv}}}", [bluez.objects])
        if message.member == "RegisterAdvertisement":
            if bluez.register_gate is not None:
                await bluez.register_gate.wait()
            if bluez.register_error is not None:
                return _error(*bluez.register_error)
            path = message.body[0]
            properties = read_properties(self.exported[path])
            bluez.registered[path] = properties
            bluez.registrations.append((message.path, properties))
            return _reply()
        if message.member == "UnregisterAdvertisement":
            path = message.body[0]
            bluez.unregistrations.append(path)
            if bluez.registered.pop(path, None) is None:
                return _error("org.bluez.Error.DoesNotExist", "Does Not Exist")
            return _reply()
        raise AssertionError(f"unexpected D-Bus call {message.member}")


def _reply(signature: str = "", body: list[Any] | None = None) -> Message:
    return Message(
        message_type=MessageType.METHOD_RETURN,
        reply_serial=1,
        signature=signature,
        body=body or [],
    )


def _error(name: str, text: str) -> Message:
    return Message(
        message_type=MessageType.ERROR,
        reply_serial=1,
        error_name=name,
        signature="s",
        body=[text],
    )


def read_properties(interface: ServiceInterface) -> dict[str, Any]:
    """Read properties the way BlueZ sees them: GetAll, marshalled on the wire."""
    replies: list[tuple[dict[str, Variant], Exception | None]] = []
    ServiceInterface._get_all_property_values(
        interface, lambda _, result, __, error: replies.append((result, error))
    )
    [(result, error)] = replies
    assert error is None, error
    wire = _reply("a{sv}", [result])._marshall(False)
    unmarshaller = Unmarshaller(io.BytesIO(wire))
    unmarshaller.unmarshall()
    properties = {}
    for name, variant in unmarshaller.message.body[0].items():
        value = variant.value
        if isinstance(value, dict):
            value = {
                key: item.value if isinstance(item, Variant) else item
                for key, item in value.items()
            }
        properties[name] = (variant.signature, value)
    return properties


@pytest.fixture
def bluez() -> Generator[FakeBlueZ]:
    """Replace the system bus with a fake BlueZ."""
    fake = FakeBlueZ()
    with patch(
        "custom_components.bluetooth_energy_transmitter.bluez._default_bus_factory",
        fake.factory,
    ):
        yield fake
