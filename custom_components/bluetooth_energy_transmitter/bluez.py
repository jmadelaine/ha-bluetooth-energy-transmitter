"""Broadcast BLE advertisements through BlueZ over the system D-Bus.

Each broadcast opens its own system bus connection, exports an
org.bluez.LEAdvertisement1 object, registers it with the adapter's
LEAdvertisingManager1, waits, then unregisters it. If Home Assistant dies
mid-broadcast the bus connection drops and BlueZ removes the advertisement
itself.

BlueZ runs alongside Home Assistant's own Bluetooth integration: advertising
uses a separate controller advertising instance and does not interfere with
scanning on the same adapter.

Adapter discovery and the advertisement lifecycle are adapted from the local
BlueZ wake backend of Xgimi-4-Home-Assistant, Copyright (c) 2022 Jiaxin,
MIT License: https://github.com/manymuch/Xgimi-4-Home-Assistant
See THIRD_PARTY_NOTICES.md.
"""

# This module intentionally does not enable postponed annotations: dbus-fast
# reads D-Bus signatures such as "s" and "a{qv}" from return annotations.

import asyncio
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field
import logging
from typing import Any
from uuid import uuid4

from dbus_fast import Message, MessageType, Variant
from dbus_fast.aio import MessageBus
from dbus_fast.constants import BusType, PropertyAccess
from dbus_fast.service import ServiceInterface, dbus_property, method

from homeassistant.exceptions import HomeAssistantError

from .advertisement import Advertisement
from .const import (
    ADAPTER_AUTO,
    ADAPTER_DEFAULT,
    DEFAULT_MAX_INTERVAL_MS,
    DEFAULT_MIN_INTERVAL_MS,
    DOMAIN,
    LEGACY_ADVERTISING_DATA_LENGTH,
)

_LOGGER = logging.getLogger(__name__)

BLUEZ_SERVICE = "org.bluez"
OBJECT_MANAGER_INTERFACE = "org.freedesktop.DBus.ObjectManager"
ADAPTER_INTERFACE = "org.bluez.Adapter1"
ADVERTISING_MANAGER_INTERFACE = "org.bluez.LEAdvertisingManager1"
ADVERTISEMENT_INTERFACE = "org.bluez.LEAdvertisement1"

ADVERTISEMENT_PATH_PREFIX = "/org/homeassistant/bluetooth_energy_transmitter"

DBUS_ERROR_UNKNOWN_OBJECT = "org.freedesktop.DBus.Error.UnknownObject"
BLUEZ_ERROR_INVALID_ARGUMENTS = "org.bluez.Error.InvalidArguments"
BLUEZ_ERROR_INVALID_LENGTH = "org.bluez.Error.InvalidLength"
BLUEZ_ERROR_DOES_NOT_EXIST = "org.bluez.Error.DoesNotExist"

BusFactory = Callable[[], MessageBus]


class BroadcastError(HomeAssistantError):
    """A broadcast failed; carries a translated, user-facing message."""

    def __init__(self, translation_key: str, **placeholders: Any) -> None:
        """Initialize with a key from the "exceptions" translations."""
        super().__init__(
            translation_domain=DOMAIN,
            translation_key=translation_key,
            translation_placeholders={
                key: str(value) for key, value in placeholders.items()
            },
        )


class DBusCallError(Exception):
    """A D-Bus method call returned an error reply."""

    def __init__(self, error_name: str, details: str) -> None:
        """Initialize with the D-Bus error name and message."""
        super().__init__(f"{error_name}: {details}" if details else error_name)
        self.error_name = error_name
        self.details = details


def _unwrap(value: Any) -> Any:
    return value.value if isinstance(value, Variant) else value


@dataclass(frozen=True, slots=True)
class BluezAdapter:
    """A local BlueZ adapter and its advertising capabilities."""

    path: str
    address: str | None
    alias: str | None
    powered: bool
    advertising: bool
    supported_instances: int | None = None
    active_instances: int | None = None
    supported_includes: tuple[str, ...] = ()
    supported_secondary_channels: tuple[str, ...] = ()
    supported_features: tuple[str, ...] = ()
    supported_capabilities: dict[str, int] = field(default_factory=dict)

    @property
    def name(self) -> str:
        """Return the kernel name, e.g. "hci0"."""
        return self.path.rsplit("/", 1)[-1]

    @property
    def identifier(self) -> str:
        """Return a stable identifier to store in config (MAC when known)."""
        return self.address or self.path

    @property
    def label(self) -> str:
        """Return a user-facing label."""
        return f"{self.name} ({self.address})" if self.address else self.name

    @property
    def has_free_instance(self) -> bool:
        """Return whether BlueZ reports a free advertising instance."""
        if self.supported_instances is None or self.active_instances is None:
            return True
        return self.active_instances < self.supported_instances

    def matches(self, requested: str) -> bool:
        """Match "hci0", "/org/bluez/hci0" or a MAC address (any case)."""
        requested = requested.strip()
        return requested in (self.name, self.path) or (
            self.address is not None and requested.upper() == self.address.upper()
        )

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-friendly summary for diagnostics and logs."""
        return {
            "path": self.path,
            "address": self.address,
            "alias": self.alias,
            "powered": self.powered,
            "advertising": self.advertising,
            "supported_instances": self.supported_instances,
            "active_instances": self.active_instances,
            "supported_includes": list(self.supported_includes),
            "supported_secondary_channels": list(self.supported_secondary_channels),
            "supported_features": list(self.supported_features),
            "supported_capabilities": dict(self.supported_capabilities),
        }


def _parse_adapters(
    managed_objects: dict[str, dict[str, dict[str, Any]]],
) -> list[BluezAdapter]:
    """Return every BlueZ adapter, advertising-capable or not."""
    adapters = []
    for path in sorted(managed_objects):
        interfaces = managed_objects[path]
        if ADAPTER_INTERFACE not in interfaces:
            continue
        adapter = {
            key: _unwrap(value) for key, value in interfaces[ADAPTER_INTERFACE].items()
        }
        manager = interfaces.get(ADVERTISING_MANAGER_INTERFACE)
        advertising = {key: _unwrap(value) for key, value in (manager or {}).items()}
        capabilities = advertising.get("SupportedCapabilities") or {}
        adapters.append(
            BluezAdapter(
                path=path,
                address=adapter.get("Address"),
                alias=adapter.get("Alias"),
                powered=bool(adapter.get("Powered", False)),
                advertising=manager is not None,
                supported_instances=advertising.get("SupportedInstances"),
                active_instances=advertising.get("ActiveInstances"),
                supported_includes=tuple(advertising.get("SupportedIncludes") or ()),
                supported_secondary_channels=tuple(
                    advertising.get("SupportedSecondaryChannels") or ()
                ),
                supported_features=tuple(advertising.get("SupportedFeatures") or ()),
                supported_capabilities={
                    key: _unwrap(value) for key, value in capabilities.items()
                },
            )
        )
    return adapters


def _select_adapter(adapters: list[BluezAdapter], requested: str) -> BluezAdapter:
    """Pick the adapter to advertise on, raising a descriptive error."""
    capable = [adapter for adapter in adapters if adapter.advertising]
    if requested == ADAPTER_AUTO:
        if not capable:
            raise BroadcastError("no_adapter")
        powered = [adapter for adapter in capable if adapter.powered]
        if not powered:
            raise BroadcastError("adapter_not_powered", adapter=capable[0].label)
        for adapter in powered:
            if adapter.has_free_instance:
                return adapter
        adapter = powered[0]
        raise BroadcastError(
            "no_free_instance",
            adapter=adapter.label,
            active=adapter.active_instances,
            supported=adapter.supported_instances,
        )

    adapter = next((a for a in adapters if a.matches(requested)), None)
    if adapter is None:
        raise BroadcastError(
            "adapter_not_found",
            adapter=requested,
            available=", ".join(a.label for a in capable) or "none",
        )
    if not adapter.advertising:
        raise BroadcastError("adapter_no_advertising", adapter=adapter.label)
    if not adapter.powered:
        raise BroadcastError("adapter_not_powered", adapter=adapter.label)
    if not adapter.has_free_instance:
        raise BroadcastError(
            "no_free_instance",
            adapter=adapter.label,
            active=adapter.active_instances,
            supported=adapter.supported_instances,
        )
    return adapter


def advertisement_properties(
    advertisement: Advertisement,
) -> dict[str, tuple[str, Any]]:
    """Return {BlueZ property: (D-Bus signature, value)} for set fields only.

    Unset fields are omitted rather than sent empty: an empty LocalName or an
    Appearance of 0 would still end up in the packet.
    """
    adv = advertisement
    props: dict[str, tuple[str, Any]] = {"Type": ("s", adv.advertisement_type)}
    if adv.manufacturer_id is not None:
        # BlueZ prepends the little-endian company ID to the payload itself.
        props["ManufacturerData"] = (
            "a{qv}",
            {adv.manufacturer_id: Variant("ay", adv.manufacturer_payload)},
        )
    if adv.service_uuids:
        props["ServiceUUIDs"] = ("as", list(adv.service_uuids))
    if adv.service_data:
        props["ServiceData"] = (
            "a{sv}",
            {uuid: Variant("ay", payload) for uuid, payload in adv.service_data},
        )
    if adv.solicit_uuids:
        props["SolicitUUIDs"] = ("as", list(adv.solicit_uuids))
    if adv.local_name is not None:
        props["LocalName"] = ("s", adv.local_name)
    if adv.appearance is not None:
        props["Appearance"] = ("q", adv.appearance)
    if adv.include_tx_power:
        props["Includes"] = ("as", ["tx-power"])
    if adv.tx_power is not None:
        props["TxPower"] = ("n", adv.tx_power)
    if adv.discoverable:
        props["Discoverable"] = ("b", True)
    # Older BlueZ releases only honor MinInterval, MaxInterval, TxPower and
    # Data when bluetoothd runs with --experimental; otherwise they're ignored.
    min_interval = adv.min_interval
    if min_interval is None:
        min_interval = min(
            DEFAULT_MIN_INTERVAL_MS, adv.max_interval or DEFAULT_MIN_INTERVAL_MS
        )
    max_interval = adv.max_interval
    if max_interval is None:
        max_interval = max(DEFAULT_MAX_INTERVAL_MS, min_interval)
    props["MinInterval"] = ("u", min_interval)
    props["MaxInterval"] = ("u", max_interval)
    if adv.data:
        props["Data"] = (
            "a{yv}",
            {ad_type: Variant("ay", payload) for ad_type, payload in adv.data},
        )
    return props


class _AdvertisementInterface(ServiceInterface):
    """Base org.bluez.LEAdvertisement1 object; properties are added per instance."""

    def __init__(self, released: asyncio.Event) -> None:
        super().__init__(ADVERTISEMENT_INTERFACE)
        self._released = released

    @method(name="Release")
    def release(self) -> None:
        """Handle BlueZ dropping the advertisement on its own."""
        self._released.set()


def _read_only_property(attribute: str, name: str, signature: str, value: Any) -> Any:
    def getter(self: ServiceInterface) -> Any:
        return value

    # dbus-fast reads the signature from the return annotation and looks the
    # getter up on the instance by its function name.
    getter.__annotations__ = {"return": signature}
    getter.__name__ = getter.__qualname__ = attribute
    return dbus_property(access=PropertyAccess.READ, name=name)(getter)


def build_advertisement_interface(
    advertisement: Advertisement, released: asyncio.Event
) -> ServiceInterface:
    """Build an interface exposing exactly the advertisement's set fields.

    dbus-fast properties are declared on the class, so each advertisement gets
    its own subclass.
    """
    namespace = {
        f"property_{name}": _read_only_property(
            f"property_{name}", name, signature, value
        )
        for name, (signature, value) in advertisement_properties(advertisement).items()
    }
    interface_class = type(
        "BroadcastAdvertisement", (_AdvertisementInterface,), namespace
    )
    return interface_class(released)


def _default_bus_factory() -> MessageBus:
    return MessageBus(bus_type=BusType.SYSTEM)


async def _async_connect(bus_factory: BusFactory) -> MessageBus:
    bus = bus_factory()
    try:
        return await bus.connect()
    except Exception as err:
        with suppress(Exception):
            bus.disconnect()
        _LOGGER.debug("Could not connect to the system D-Bus: %r", err)
        raise BroadcastError(
            "dbus_unavailable", error=str(err) or type(err).__name__
        ) from err


async def _async_call(bus: MessageBus, message: Message) -> Message:
    try:
        reply = await bus.call(message)
    except Exception as err:
        _LOGGER.debug(
            "D-Bus call %s.%s failed: %r", message.interface, message.member, err
        )
        raise BroadcastError(
            "dbus_unavailable", error=str(err) or type(err).__name__
        ) from err
    if reply is None:
        raise BroadcastError("dbus_unavailable", error="no reply")
    if reply.message_type == MessageType.ERROR:
        details = str(reply.body[0]) if reply.body else ""
        _LOGGER.debug(
            "D-Bus call %s.%s on %s returned %s: %s",
            message.interface,
            message.member,
            message.path,
            reply.error_name,
            details,
        )
        raise DBusCallError(reply.error_name or "unknown", details)
    return reply


async def _async_get_adapters(bus: MessageBus) -> list[BluezAdapter]:
    try:
        reply = await _async_call(
            bus,
            Message(
                destination=BLUEZ_SERVICE,
                path="/",
                interface=OBJECT_MANAGER_INTERFACE,
                member="GetManagedObjects",
            ),
        )
    except DBusCallError as err:
        raise BroadcastError("bluez_unavailable", error=err) from err
    return _parse_adapters(reply.body[0])


def _registration_error(
    err: DBusCallError, adapter: BluezAdapter, advertisement: Advertisement
) -> BroadcastError:
    """Translate a RegisterAdvertisement error reply."""
    if "maximum advertisements" in err.details.lower():
        return BroadcastError(
            "no_free_instance",
            adapter=adapter.label,
            active=adapter.active_instances,
            supported=adapter.supported_instances,
        )
    if err.error_name == BLUEZ_ERROR_INVALID_LENGTH:
        return BroadcastError(
            "advertisement_too_long",
            adapter=adapter.label,
            length=advertisement.estimated_length,
            limit=adapter.supported_capabilities.get(
                "MaxAdvLen", LEGACY_ADVERTISING_DATA_LENGTH
            ),
        )
    if err.error_name == BLUEZ_ERROR_INVALID_ARGUMENTS:
        return BroadcastError(
            "advertisement_rejected", adapter=adapter.label, error=err
        )
    if err.error_name in (BLUEZ_ERROR_DOES_NOT_EXIST, DBUS_ERROR_UNKNOWN_OBJECT):
        return BroadcastError("adapter_removed", adapter=adapter.label)
    return BroadcastError("registration_failed", adapter=adapter.label, error=err)


@dataclass(slots=True)
class _ActiveBroadcast:
    task: asyncio.Task[None]
    registered: asyncio.Future[BluezAdapter]


class Broadcaster:
    """Send advertisements, sharing in-flight duplicates."""

    def __init__(
        self,
        default_adapter: str = ADAPTER_AUTO,
        bus_factory: BusFactory | None = None,
    ) -> None:
        """Initialize with the adapter used when a request names none."""
        self.default_adapter = default_adapter
        self._bus_factory = bus_factory or _default_bus_factory
        self._closing = asyncio.Event()
        self._active: dict[tuple[Advertisement, str], _ActiveBroadcast] = {}

    @property
    def active_count(self) -> int:
        """Return the number of advertisements currently on air."""
        return len(self._active)

    async def async_get_adapters(self) -> list[BluezAdapter]:
        """Return all BlueZ adapters with their advertising capabilities."""
        bus = await _async_connect(self._bus_factory)
        try:
            return await _async_get_adapters(bus)
        finally:
            bus.disconnect()

    async def async_broadcast(
        self,
        advertisement: Advertisement,
        duration: float,
        adapter: str | None = None,
    ) -> BluezAdapter:
        """Start broadcasting and return once BlueZ has accepted it.

        The advertisement stays on air for `duration` seconds in the background.
        If an identical advertisement is already on air on the same adapter,
        this joins it instead of using another advertising instance.
        """
        if self._closing.is_set():
            raise BroadcastError("broadcaster_closed")
        if not adapter or adapter == ADAPTER_DEFAULT:
            adapter = self.default_adapter
        key = (advertisement, adapter.strip().upper())

        if (active := self._active.get(key)) is not None:
            _LOGGER.debug(
                "Identical advertisement already on air (adapter=%s); joining it: %s",
                adapter,
                advertisement.describe(),
            )
            return await asyncio.shield(active.registered)

        registered: asyncio.Future[BluezAdapter] = (
            asyncio.get_running_loop().create_future()
        )
        # Mark the exception as retrieved if every waiter was cancelled.
        registered.add_done_callback(
            lambda future: future.cancelled() or future.exception()
        )
        task = asyncio.create_task(
            self._async_run(advertisement, duration, adapter, registered),
            name=f"{DOMAIN} broadcast",
        )
        self._active[key] = _ActiveBroadcast(task, registered)
        task.add_done_callback(lambda _: self._active.pop(key, None))
        return await asyncio.shield(registered)

    async def async_close(self) -> None:
        """Stop every advertisement and wait for cleanup."""
        self._closing.set()
        if tasks := [active.task for active in self._active.values()]:
            _LOGGER.debug("Stopping %s active advertisement(s)", len(tasks))
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _async_run(
        self,
        advertisement: Advertisement,
        duration: float,
        requested: str,
        registered: asyncio.Future[BluezAdapter],
    ) -> None:
        try:
            await self._async_advertise(advertisement, duration, requested, registered)
        except asyncio.CancelledError:
            if not registered.done():
                registered.cancel()
            raise
        except Exception as err:
            if not registered.done():
                registered.set_exception(err)
            else:
                _LOGGER.warning("BLE advertisement failed after registration: %s", err)

    async def _async_advertise(
        self,
        advertisement: Advertisement,
        duration: float,
        requested: str,
        registered: asyncio.Future[BluezAdapter],
    ) -> None:
        _LOGGER.debug(
            "Broadcasting for %ss (adapter=%s, ~%s bytes advertising data): %s",
            duration,
            requested,
            advertisement.estimated_length,
            advertisement.describe(),
        )
        bus = await _async_connect(self._bus_factory)
        try:
            adapters = await _async_get_adapters(bus)
            adapter = _select_adapter(adapters, requested)
            _LOGGER.debug("Selected adapter %s: %s", adapter.label, adapter.as_dict())
            if advertisement.estimated_length > LEGACY_ADVERTISING_DATA_LENGTH:
                _LOGGER.warning(
                    "Advertisement is about %s bytes, over the %s-byte legacy limit; "
                    "BlueZ will use extended advertising, which Bluetooth 4.x "
                    "receivers can't see: %s",
                    advertisement.estimated_length,
                    LEGACY_ADVERTISING_DATA_LENGTH,
                    advertisement.describe(),
                )

            released = asyncio.Event()
            interface = build_advertisement_interface(advertisement, released)
            path = f"{ADVERTISEMENT_PATH_PREFIX}/advertisement_{uuid4().hex}"
            bus.export(path, interface)
            try:
                await self._async_register(bus, adapter, path, advertisement)
                registered.set_result(adapter)
                try:
                    await self._async_wait(bus, duration, released)
                finally:
                    if bus.connected and not released.is_set():
                        await self._async_unregister(bus, adapter, path)
            finally:
                with suppress(Exception):
                    bus.unexport(path, interface)
        finally:
            bus.disconnect()
            _LOGGER.debug("Broadcast finished on %s", requested)

    async def _async_register(
        self,
        bus: MessageBus,
        adapter: BluezAdapter,
        path: str,
        advertisement: Advertisement,
    ) -> None:
        _LOGGER.debug("Registering advertisement %s on %s", path, adapter.path)
        try:
            await _async_call(
                bus,
                Message(
                    destination=BLUEZ_SERVICE,
                    path=adapter.path,
                    interface=ADVERTISING_MANAGER_INTERFACE,
                    member="RegisterAdvertisement",
                    signature="oa{sv}",
                    body=[path, {}],
                ),
            )
        except DBusCallError as err:
            raise _registration_error(err, adapter, advertisement) from err
        _LOGGER.debug("Advertisement %s registered on %s", path, adapter.path)

    async def _async_wait(
        self, bus: MessageBus, duration: float, released: asyncio.Event
    ) -> None:
        """Wait for the duration, shutdown, BlueZ releasing it, or bus loss."""
        waiters = {
            asyncio.create_task(asyncio.sleep(duration)): "duration",
            asyncio.create_task(self._closing.wait()): "closing",
            asyncio.create_task(released.wait()): "released",
            asyncio.create_task(bus.wait_for_disconnect()): "disconnected",
        }
        try:
            done, _ = await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for waiter in waiters:
                waiter.cancel()
            await asyncio.gather(*waiters, return_exceptions=True)
        reason = waiters[next(iter(done))]
        if reason == "closing":
            _LOGGER.debug("Stopping advertisement early: integration unloading")
        elif reason == "released":
            _LOGGER.warning(
                "BlueZ released the advertisement before %ss elapsed "
                "(adapter powered off or removed?)",
                duration,
            )
        elif reason == "disconnected":
            _LOGGER.warning("Lost the D-Bus connection while advertising")

    async def _async_unregister(
        self, bus: MessageBus, adapter: BluezAdapter, path: str
    ) -> None:
        _LOGGER.debug("Unregistering advertisement %s from %s", path, adapter.path)
        try:
            await _async_call(
                bus,
                Message(
                    destination=BLUEZ_SERVICE,
                    path=adapter.path,
                    interface=ADVERTISING_MANAGER_INTERFACE,
                    member="UnregisterAdvertisement",
                    signature="o",
                    body=[path],
                ),
            )
        except (DBusCallError, BroadcastError) as err:
            # Disconnecting the bus makes BlueZ drop it anyway.
            _LOGGER.debug("Could not unregister advertisement %s: %s", path, err)
