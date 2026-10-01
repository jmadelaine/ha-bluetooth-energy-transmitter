"""Turn an advertisement copied from Home Assistant's Advertisement Monitor into a signal.

The monitor's device dialog has a "Copy to clipboard" button that copies JSON:

    {"name": "...", "address": "...", "manufacturer_data": {"70": "<hex>"},
     "service_data": {"<uuid>": "<hex>"}, "service_uuids": ["<uuid>"],
     "connectable": true, "tx_power": null, "raw": "<hex>" | null, ...}

`raw` is the latest advertising packet as received. When present it is
decoded and replayed as-is, because it is the only place fields such as
appearance show up, and the decoded fields also include scan response data
that would not fit in the packet. Otherwise the decoded fields are used. A bare
hex string is treated as `raw`.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

import voluptuous as vol

from .advertisement import normalize_hex, normalize_uuid
from .const import (
    ADVERTISEMENT_TYPE_BROADCAST,
    ADVERTISEMENT_TYPE_PERIPHERAL,
    CONF_ADVERTISEMENT_TYPE,
    CONF_APPEARANCE,
    CONF_DATA,
    CONF_INCLUDE_TX_POWER,
    CONF_LOCAL_NAME,
    CONF_MANUFACTURER_ID,
    CONF_PAYLOAD,
    CONF_SERVICE_DATA,
    CONF_SERVICE_UUIDS,
    CONF_SOLICIT_UUIDS,
)

# AD types (Bluetooth Assigned Numbers, "Common Data Types").
AD_FLAGS = 0x01
AD_UUIDS_16 = (0x02, 0x03)
AD_UUIDS_32 = (0x04, 0x05)
AD_UUIDS_128 = (0x06, 0x07)
AD_NAME = (0x08, 0x09)
AD_TX_POWER = 0x0A
AD_SOLICIT_16 = 0x14
AD_SOLICIT_128 = 0x15
AD_SERVICE_DATA_16 = 0x16
AD_APPEARANCE = 0x19
AD_SOLICIT_32 = 0x1F
AD_SERVICE_DATA_32 = 0x20
AD_SERVICE_DATA_128 = 0x21
AD_MANUFACTURER = 0xFF


class AdvertisementImportError(Exception):
    """The pasted text couldn't be turned into a signal."""

    def __init__(self, key: str, **placeholders: str) -> None:
        """Initialize with a config flow error key."""
        super().__init__(key)
        self.key = key
        self.placeholders = placeholders


def _uuid_from_le(value: bytes) -> str:
    if len(value) == 16:
        return str(UUID(bytes=value[::-1]))
    return normalize_uuid(f"{int.from_bytes(value, 'little'):0{len(value) * 2}x}")


def _split(value: bytes, size: int) -> list[bytes]:
    if not value or len(value) % size:
        raise AdvertisementImportError("invalid_raw")
    return [value[i : i + size] for i in range(0, len(value), size)]


def parse_raw(raw: bytes) -> tuple[dict[str, Any], dict[int, bytes]]:
    """Decode AD structures into signal fields.

    Returns (config, manufacturer data). Manufacturer data is returned
    separately so the caller can reject packets with several company IDs.
    """
    config: dict[str, Any] = {}
    uuids: list[str] = []
    solicit: list[str] = []
    service_data: dict[str, str] = {}
    manufacturer: dict[int, bytes] = {}
    other: dict[int, str] = {}

    index = 0
    while index < len(raw):
        length = raw[index]
        if length == 0:  # zero padding after the last structure
            break
        if index + 1 + length > len(raw):
            raise AdvertisementImportError("invalid_raw")
        ad_type = raw[index + 1]
        value = raw[index + 2 : index + 1 + length]
        index += 1 + length

        if ad_type == AD_FLAGS:
            continue  # BlueZ and the kernel set the flags
        if ad_type in (*AD_UUIDS_16, *AD_UUIDS_32, *AD_UUIDS_128):
            size = 2 if ad_type in AD_UUIDS_16 else 4 if ad_type in AD_UUIDS_32 else 16
            uuids += [_uuid_from_le(chunk) for chunk in _split(value, size)]
        elif ad_type in (AD_SOLICIT_16, AD_SOLICIT_32, AD_SOLICIT_128):
            size = {AD_SOLICIT_16: 2, AD_SOLICIT_32: 4, AD_SOLICIT_128: 16}[ad_type]
            solicit += [_uuid_from_le(chunk) for chunk in _split(value, size)]
        elif ad_type in AD_NAME:
            config[CONF_LOCAL_NAME] = value.decode("utf-8", errors="replace")
        elif ad_type == AD_TX_POWER:
            config[CONF_INCLUDE_TX_POWER] = True
        elif ad_type == AD_APPEARANCE:
            if len(value) != 2:
                raise AdvertisementImportError("invalid_raw")
            config[CONF_APPEARANCE] = int.from_bytes(value, "little")
        elif ad_type in (AD_SERVICE_DATA_16, AD_SERVICE_DATA_32, AD_SERVICE_DATA_128):
            size = {
                AD_SERVICE_DATA_16: 2,
                AD_SERVICE_DATA_32: 4,
                AD_SERVICE_DATA_128: 16,
            }[ad_type]
            if len(value) < size:
                raise AdvertisementImportError("invalid_raw")
            service_data[_uuid_from_le(value[:size])] = value[size:].hex()
        elif ad_type == AD_MANUFACTURER:
            if len(value) < 2:
                raise AdvertisementImportError("invalid_raw")
            manufacturer[int.from_bytes(value[:2], "little")] = value[2:]
        else:
            other[ad_type] = value.hex()

    if uuids:
        config[CONF_SERVICE_UUIDS] = list(dict.fromkeys(uuids))
    if solicit:
        config[CONF_SOLICIT_UUIDS] = list(dict.fromkeys(solicit))
    if service_data:
        config[CONF_SERVICE_DATA] = service_data
    if other:
        config[CONF_DATA] = other
    return config, manufacturer


def _parse_monitor_json(
    entry: dict[str, Any],
) -> tuple[dict[str, Any], dict[int, bytes]]:
    """Use the decoded fields when the monitor had no raw packet."""
    try:
        manufacturer = {
            int(company_id): bytes.fromhex(payload)
            for company_id, payload in (entry.get("manufacturer_data") or {}).items()
        }
        config: dict[str, Any] = {}
        if service_uuids := entry.get("service_uuids"):
            config[CONF_SERVICE_UUIDS] = [
                normalize_uuid(uuid) for uuid in service_uuids
            ]
        if service_data := entry.get("service_data"):
            config[CONF_SERVICE_DATA] = {
                normalize_uuid(uuid): normalize_hex(payload)
                for uuid, payload in service_data.items()
            }
    except (AttributeError, TypeError, ValueError, vol.Invalid) as err:
        raise AdvertisementImportError("invalid_monitor_json") from err
    # The monitor shows the address as the name when the device sent none.
    name = entry.get("name")
    if isinstance(name, str) and name and name != entry.get("address"):
        config[CONF_LOCAL_NAME] = name
    if entry.get("tx_power") is not None:
        config[CONF_INCLUDE_TX_POWER] = True
    return config, manufacturer


def parse_monitor_advertisement(text: str) -> tuple[str | None, dict[str, Any]]:
    """Return (suggested signal name, signal fields) from pasted text."""
    text = text.strip()
    entry: dict[str, Any] | None = None
    if text.startswith("{"):
        try:
            entry = json.loads(text)
        except ValueError as err:
            raise AdvertisementImportError("invalid_monitor_json") from err
        if not isinstance(entry, dict):
            raise AdvertisementImportError("invalid_monitor_json")
        raw_hex = entry.get("raw")
    else:
        raw_hex = text

    if raw_hex:
        try:
            raw = bytes.fromhex(normalize_hex(raw_hex))
        except vol.Invalid as err:
            raise AdvertisementImportError("invalid_raw") from err
        config, manufacturer = parse_raw(raw)
        # The monitor merges in the scan response, which isn't in `raw`. Only
        # the name is taken from it: BlueZ puts LocalName in the scan response
        # too, whereas extra UUIDs would grow the advertising packet itself.
        if entry and CONF_LOCAL_NAME not in config:
            json_name = _parse_monitor_json(entry)[0].get(CONF_LOCAL_NAME)
            if json_name:
                config[CONF_LOCAL_NAME] = json_name
    else:
        config, manufacturer = _parse_monitor_json(entry or {})

    if len(manufacturer) > 1:
        raise AdvertisementImportError(
            "multiple_manufacturers",
            ids=", ".join(f"0x{company_id:04x}" for company_id in manufacturer),
        )
    if manufacturer:
        [(company_id, payload)] = manufacturer.items()
        config[CONF_MANUFACTURER_ID] = company_id
        config[CONF_PAYLOAD] = payload.hex()
    if not config:
        raise AdvertisementImportError("empty_advertisement")

    connectable = entry.get("connectable", True) if entry else True
    config[CONF_ADVERTISEMENT_TYPE] = (
        ADVERTISEMENT_TYPE_PERIPHERAL if connectable else ADVERTISEMENT_TYPE_BROADCAST
    )
    name = entry.get("name") if entry else None
    if not isinstance(name, str) or not name or name == (entry or {}).get("address"):
        name = config.get(CONF_LOCAL_NAME)
    return name, config
