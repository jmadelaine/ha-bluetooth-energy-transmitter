"""Constants for the Bluetooth Energy Transmitter integration."""

from typing import Final

DOMAIN: Final = "bluetooth_energy_transmitter"

SUBENTRY_TYPE_SIGNAL: Final = "signal"

SERVICE_SEND: Final = "send"

# Adapter selection. The integration-wide default is either "auto" or an
# adapter identifier; a saved signal may defer to the integration default.
CONF_ADAPTER: Final = "adapter"
ADAPTER_AUTO: Final = "auto"
ADAPTER_DEFAULT: Final = "default"

# Advertisement fields, shared by the send action and saved signals.
CONF_ADVERTISEMENT_TYPE: Final = "advertisement_type"
CONF_MANUFACTURER_ID: Final = "manufacturer_id"
CONF_PAYLOAD: Final = "payload"
CONF_LOCAL_NAME: Final = "local_name"
CONF_SERVICE_UUIDS: Final = "service_uuids"
CONF_SERVICE_DATA: Final = "service_data"
CONF_SOLICIT_UUIDS: Final = "solicit_uuids"
CONF_APPEARANCE: Final = "appearance"
CONF_INCLUDE_TX_POWER: Final = "include_tx_power"
CONF_TX_POWER: Final = "tx_power"
CONF_DISCOVERABLE: Final = "discoverable"
CONF_MIN_INTERVAL: Final = "min_interval"
CONF_MAX_INTERVAL: Final = "max_interval"
CONF_DATA: Final = "data"
CONF_DURATION: Final = "duration"

ADVERTISEMENT_TYPE_PERIPHERAL: Final = "peripheral"
ADVERTISEMENT_TYPE_BROADCAST: Final = "broadcast"
ADVERTISEMENT_TYPES: Final = [
    ADVERTISEMENT_TYPE_PERIPHERAL,
    ADVERTISEMENT_TYPE_BROADCAST,
]

DEFAULT_DURATION: Final = 4.0
MIN_DURATION: Final = 1.0
MAX_DURATION: Final = 10.0

MIN_TX_POWER: Final = -127
MAX_TX_POWER: Final = 20

# BlueZ MinInterval/MaxInterval are in milliseconds; the controller accepts
# 0x0020-0x4000 units of 0.625 ms.
MIN_INTERVAL_MS: Final = 20
MAX_INTERVAL_MS: Final = 10240

# Legacy (Bluetooth 4.x) advertising PDUs carry at most 31 bytes of data.
LEGACY_ADVERTISING_DATA_LENGTH: Final = 31
