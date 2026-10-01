"""Advertisement model and input normalization.

Everything here is independent of D-Bus so it can be shared by the send
action, the config flow and the button entities.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import re
from typing import Any, Self
from uuid import UUID

import voluptuous as vol

from .const import (
    ADVERTISEMENT_TYPE_PERIPHERAL,
    ADVERTISEMENT_TYPES,
    CONF_ADVERTISEMENT_TYPE,
    CONF_APPEARANCE,
    CONF_DATA,
    CONF_DISCOVERABLE,
    CONF_INCLUDE_TX_POWER,
    CONF_LOCAL_NAME,
    CONF_MANUFACTURER_ID,
    CONF_MAX_INTERVAL,
    CONF_MIN_INTERVAL,
    CONF_PAYLOAD,
    CONF_SERVICE_DATA,
    CONF_SERVICE_UUIDS,
    CONF_SOLICIT_UUIDS,
    CONF_TX_POWER,
    MAX_INTERVAL_MS,
    MAX_TX_POWER,
    MIN_INTERVAL_MS,
    MIN_TX_POWER,
)

BASE_UUID_SUFFIX = "-0000-1000-8000-00805f9b34fb"

_HEX_SEPARATORS = re.compile(r"[\s:,\-_]+")
_MAPPING_SEPARATORS = re.compile(r"[\n;]+")
_LIST_SEPARATORS = re.compile(r"[\s,;]+")
_SHORT_UUID = re.compile(r"[0-9a-f]{4}|[0-9a-f]{8}")


def normalize_hex(value: Any) -> str:
    """Return lowercase hex without separators.

    Accepts "0a1b2c", "0x0a1b2c", "0A 1B 2C", "0a:1b:2c" and "0x0a 0x1b".
    """
    if isinstance(value, bytes | bytearray):
        return bytes(value).hex()
    if not isinstance(value, str):
        raise vol.Invalid("expected a hex string")
    tokens = [token for token in _HEX_SEPARATORS.split(value.strip()) if token]
    digits = "".join(
        token[2:] if token[:2].lower() == "0x" else token for token in tokens
    )
    if len(digits) % 2:
        raise vol.Invalid("hex string has an odd number of digits")
    try:
        return bytes.fromhex(digits).hex()
    except ValueError as err:
        raise vol.Invalid("not a valid hex string") from err


def _parse_int(value: Any) -> int:
    """Parse an integer given as a number, "0x"-prefixed hex or decimal text."""
    if isinstance(value, bool):
        raise vol.Invalid("expected an integer")
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not value.is_integer():
            raise vol.Invalid("expected an integer")
        return int(value)
    if not isinstance(value, str):
        raise vol.Invalid("expected an integer")
    text = value.strip()
    if text[:2].lower() == "0x":
        try:
            return int(text[2:], 16)
        except ValueError as err:
            raise vol.Invalid("not a valid hex number") from err
    if not text.isdigit():
        raise vol.Invalid("use decimal (70) or 0x-prefixed hex (0x0046)")
    if len(text) > 1 and text[0] == "0":
        # "0046" is almost certainly meant as hex but would parse as decimal 46.
        raise vol.Invalid(
            f"ambiguous number {text!r}: write 0x{text} for hex or "
            f"{int(text)} for decimal"
        )
    return int(text)


def _int_in_range(minimum: int, maximum: int) -> vol.All:
    return vol.All(_parse_int, vol.Range(min=minimum, max=maximum))


uint8 = _int_in_range(0, 0xFF)
uint16 = _int_in_range(0, 0xFFFF)


def normalize_uuid(value: Any) -> str:
    """Return a lowercase 128-bit UUID string.

    16-bit ("1812", "0x1812") and 32-bit UUIDs are expanded with the Bluetooth
    base UUID; BlueZ shortens them again when building the packet.
    """
    if not isinstance(value, str):
        raise vol.Invalid("expected a UUID string")
    text = value.strip().lower()
    if text.startswith("0x"):
        text = text[2:]
    if _SHORT_UUID.fullmatch(text):
        return f"{text:0>8}{BASE_UUID_SUFFIX}"
    try:
        return str(UUID(text))
    except ValueError as err:
        raise vol.Invalid(f"not a valid Bluetooth UUID: {value!r}") from err


def uuid_list(value: Any) -> list[str]:
    """Return a de-duplicated list of UUIDs from a list or separated string."""
    if value is None:
        return []
    if isinstance(value, str):
        value = _LIST_SEPARATORS.split(value)
    if not isinstance(value, list | tuple):
        raise vol.Invalid("expected a list of UUIDs")
    uuids: list[str] = []
    for item in value:
        if isinstance(item, str) and not item.strip():
            continue
        uuid = normalize_uuid(item)
        if uuid not in uuids:
            uuids.append(uuid)
    return uuids


def _parse_mapping(value: Any) -> dict[Any, Any]:
    """Return a dict from a mapping or "key=value" lines."""
    if isinstance(value, Mapping):
        return dict(value)
    if not isinstance(value, str):
        raise vol.Invalid("expected a mapping or key=hex lines")
    result: dict[str, str] = {}
    for line in _MAPPING_SEPARATORS.split(value):
        if not line.strip():
            continue
        key, separator, hex_value = line.partition("=")
        if not separator:
            raise vol.Invalid(f"expected key=hex, got {line.strip()!r}")
        result[key.strip()] = hex_value.strip()
    return result


def service_data_map(value: Any) -> dict[str, str]:
    """Return {128-bit UUID: hex} from a mapping or "uuid=hex" lines."""
    return {
        normalize_uuid(str(key)): normalize_hex(hex_value)
        for key, hex_value in _parse_mapping(value).items()
    }


def ad_data_map(value: Any) -> dict[int, str]:
    """Return {AD type: hex} from a mapping or "type=hex" lines."""
    return {
        uint8(key): normalize_hex(hex_value)
        for key, hex_value in _parse_mapping(value).items()
    }


def _check_intervals(config: dict[str, Any]) -> dict[str, Any]:
    minimum = config.get(CONF_MIN_INTERVAL)
    maximum = config.get(CONF_MAX_INTERVAL)
    if minimum is not None and maximum is not None and minimum > maximum:
        raise vol.Invalid(
            "min_interval must not be greater than max_interval",
            path=[CONF_MIN_INTERVAL],
        )
    return config


# Fields that describe the advertisement itself. Values are normalized so the
# schema can be re-applied to already-validated (or JSON round-tripped) data.
ADVERTISEMENT_FIELDS: dict[vol.Marker, Any] = {
    vol.Optional(
        CONF_ADVERTISEMENT_TYPE, default=ADVERTISEMENT_TYPE_PERIPHERAL
    ): vol.In(ADVERTISEMENT_TYPES),
    vol.Inclusive(CONF_MANUFACTURER_ID, "manufacturer_data"): uint16,
    vol.Inclusive(CONF_PAYLOAD, "manufacturer_data"): normalize_hex,
    vol.Optional(CONF_LOCAL_NAME): vol.All(str, vol.Length(min=1)),
    vol.Optional(CONF_SERVICE_UUIDS): uuid_list,
    vol.Optional(CONF_SERVICE_DATA): service_data_map,
    vol.Optional(CONF_SOLICIT_UUIDS): uuid_list,
    vol.Optional(CONF_APPEARANCE): uint16,
    vol.Optional(CONF_INCLUDE_TX_POWER): bool,
    vol.Optional(CONF_TX_POWER): _int_in_range(MIN_TX_POWER, MAX_TX_POWER),
    vol.Optional(CONF_DISCOVERABLE): bool,
    vol.Optional(CONF_MIN_INTERVAL): _int_in_range(MIN_INTERVAL_MS, MAX_INTERVAL_MS),
    vol.Optional(CONF_MAX_INTERVAL): _int_in_range(MIN_INTERVAL_MS, MAX_INTERVAL_MS),
    vol.Optional(CONF_DATA): ad_data_map,
}

ADVERTISEMENT_SCHEMA = vol.All(
    vol.Schema(ADVERTISEMENT_FIELDS, extra=vol.ALLOW_EXTRA), _check_intervals
)


def _uuid_size(uuid: str) -> int:
    if uuid.endswith(BASE_UUID_SUFFIX):
        return 2 if uuid.startswith("0000") else 4
    return 16


def _uuid_list_length(uuids: tuple[str, ...]) -> int:
    """Return the AD length of UUID lists, grouped by UUID size like BlueZ."""
    sizes = [_uuid_size(uuid) for uuid in uuids]
    return sum(2 + sizes.count(size) * size for size in set(sizes))


@dataclass(frozen=True, slots=True)
class Advertisement:
    """A user-defined BLE advertisement.

    Hashable so identical in-flight advertisements can be de-duplicated.
    """

    advertisement_type: str = ADVERTISEMENT_TYPE_PERIPHERAL
    manufacturer_id: int | None = None
    manufacturer_payload: bytes = b""
    local_name: str | None = None
    service_uuids: tuple[str, ...] = ()
    service_data: tuple[tuple[str, bytes], ...] = ()
    solicit_uuids: tuple[str, ...] = ()
    appearance: int | None = None
    include_tx_power: bool = False
    tx_power: int | None = None
    discoverable: bool = False
    min_interval: int | None = None
    max_interval: int | None = None
    data: tuple[tuple[int, bytes], ...] = ()

    @classmethod
    def from_config(cls, config: Mapping[str, Any]) -> Self:
        """Build an advertisement from action data or a saved signal."""
        config = ADVERTISEMENT_SCHEMA(dict(config))
        return cls(
            advertisement_type=config[CONF_ADVERTISEMENT_TYPE],
            manufacturer_id=config.get(CONF_MANUFACTURER_ID),
            manufacturer_payload=bytes.fromhex(config.get(CONF_PAYLOAD, "")),
            local_name=config.get(CONF_LOCAL_NAME),
            service_uuids=tuple(config.get(CONF_SERVICE_UUIDS, ())),
            service_data=tuple(
                (uuid, bytes.fromhex(hex_value))
                for uuid, hex_value in config.get(CONF_SERVICE_DATA, {}).items()
            ),
            solicit_uuids=tuple(config.get(CONF_SOLICIT_UUIDS, ())),
            appearance=config.get(CONF_APPEARANCE),
            include_tx_power=config.get(CONF_INCLUDE_TX_POWER, False),
            tx_power=config.get(CONF_TX_POWER),
            discoverable=config.get(CONF_DISCOVERABLE, False),
            min_interval=config.get(CONF_MIN_INTERVAL),
            max_interval=config.get(CONF_MAX_INTERVAL),
            data=tuple(
                (ad_type, bytes.fromhex(hex_value))
                for ad_type, hex_value in config.get(CONF_DATA, {}).items()
            ),
        )

    @property
    def estimated_length(self) -> int:
        """Estimate the advertising data length in bytes.

        Approximate. BlueZ puts the local name in the scan response, so it is
        excluded; appearance stays in the advertising data.
        """
        length = 3 if self.advertisement_type == ADVERTISEMENT_TYPE_PERIPHERAL else 0
        if self.manufacturer_id is not None:
            length += 4 + len(self.manufacturer_payload)
        length += _uuid_list_length(self.service_uuids)
        length += _uuid_list_length(self.solicit_uuids)
        length += sum(
            2 + _uuid_size(uuid) + len(payload) for uuid, payload in self.service_data
        )
        if self.appearance is not None:
            length += 4
        if self.include_tx_power:
            length += 3
        length += sum(2 + len(payload) for _, payload in self.data)
        return length

    def describe(self) -> str:
        """Return a compact one-line description for logs."""
        parts = [f"type={self.advertisement_type}"]
        if self.manufacturer_id is not None:
            parts.append(
                f"manufacturer=0x{self.manufacturer_id:04x}:"
                f"{self.manufacturer_payload.hex() or '<empty>'}"
            )
        if self.local_name is not None:
            parts.append(f"local_name={self.local_name!r}")
        if self.service_uuids:
            parts.append(f"service_uuids={list(self.service_uuids)}")
        if self.service_data:
            parts.append(
                "service_data="
                + str({uuid: payload.hex() for uuid, payload in self.service_data})
            )
        if self.solicit_uuids:
            parts.append(f"solicit_uuids={list(self.solicit_uuids)}")
        if self.appearance is not None:
            parts.append(f"appearance={self.appearance}")
        if self.include_tx_power:
            parts.append("include_tx_power")
        if self.tx_power is not None:
            parts.append(f"tx_power={self.tx_power}dBm")
        if self.discoverable:
            parts.append("discoverable")
        if self.min_interval is not None or self.max_interval is not None:
            parts.append(f"interval={self.min_interval}-{self.max_interval}ms")
        if self.data:
            parts.append(
                "data="
                + str(
                    {
                        f"0x{ad_type:02x}": payload.hex()
                        for ad_type, payload in self.data
                    }
                )
            )
        return " ".join(parts)
