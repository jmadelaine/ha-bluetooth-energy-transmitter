"""Config flow: the integration entry, its options, and saved signals."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigEntryState,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryFlow,
    OptionsFlow,
    SubentryFlowResult,
)
from homeassistant.const import CONF_NAME
from homeassistant.core import callback
from homeassistant.data_entry_flow import section
from homeassistant.helpers.selector import (
    BooleanSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
)

from .advertisement import (
    ad_data_map,
    normalize_hex,
    service_data_map,
    uint16,
    uuid_list,
)
from .bluez import BluezAdapter, Broadcaster, BroadcastError
from .const import (
    ADAPTER_AUTO,
    ADAPTER_DEFAULT,
    ADVERTISEMENT_TYPE_PERIPHERAL,
    ADVERTISEMENT_TYPES,
    CONF_ADAPTER,
    CONF_ADVERTISEMENT_TYPE,
    CONF_APPEARANCE,
    CONF_DATA,
    CONF_DISCOVERABLE,
    CONF_DURATION,
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
    DEFAULT_DURATION,
    DOMAIN,
    MAX_DURATION,
    MAX_INTERVAL_MS,
    MAX_TX_POWER,
    MIN_DURATION,
    MIN_INTERVAL_MS,
    MIN_TX_POWER,
    SUBENTRY_TYPE_SIGNAL,
)
from .monitor_import import AdvertisementImportError, parse_monitor_advertisement

_LOGGER = logging.getLogger(__name__)

SECTION_ADVANCED = "advanced"
CONF_ADVERTISEMENT = "advertisement"


async def _async_discover(entry: ConfigEntry | None = None) -> list[BluezAdapter]:
    """Return advertising-capable adapters, or [] if BlueZ is unreachable."""
    broadcaster = (
        entry.runtime_data
        if entry is not None and entry.state is ConfigEntryState.LOADED
        else Broadcaster()
    )
    try:
        adapters = await broadcaster.async_get_adapters()
    except BroadcastError as err:
        _LOGGER.debug("Adapter discovery failed: %s", err)
        return []
    return [adapter for adapter in adapters if adapter.advertising]


def _adapter_selector(
    adapters: list[BluezAdapter], first: str, current: str | None
) -> SelectSelector:
    """Offer `first` ("auto" or "default") plus each discovered adapter."""
    options = [SelectOptionDict(value=first, label=first)]
    options += [
        SelectOptionDict(value=adapter.identifier, label=adapter.label)
        for adapter in adapters
    ]
    if current and current not in (option["value"] for option in options):
        options.append(SelectOptionDict(value=current, label=f"{current} (not found)"))
    return SelectSelector(
        SelectSelectorConfig(
            options=options,
            custom_value=True,
            mode=SelectSelectorMode.DROPDOWN,
            translation_key=CONF_ADAPTER,
        )
    )


class BluetoothEnergyTransmitterConfigFlow(ConfigFlow, domain=DOMAIN):
    """Set up the integration (once per Home Assistant instance)."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick the default adapter."""
        if user_input is not None:
            return self.async_create_entry(
                title="Bluetooth Energy Transmitter", data={}, options=user_input
            )
        if not (adapters := await _async_discover()):
            return self.async_abort(reason="no_adapters")
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_ADAPTER, default=ADAPTER_AUTO): _adapter_selector(
                        adapters, ADAPTER_AUTO, None
                    )
                }
            ),
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Return the options flow."""
        return BluetoothEnergyTransmitterOptionsFlow()

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        """Saved signals are subentries."""
        return {SUBENTRY_TYPE_SIGNAL: SignalSubentryFlow}


class BluetoothEnergyTransmitterOptionsFlow(OptionsFlow):
    """Change the default adapter."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick the default adapter."""
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        current = self.config_entry.options.get(CONF_ADAPTER, ADAPTER_AUTO)
        adapters = await _async_discover(self.config_entry)
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_ADAPTER, default=current): _adapter_selector(
                        adapters, ADAPTER_AUTO, current
                    )
                }
            ),
        )


def _signal_schema(
    adapters: list[BluezAdapter], current_adapter: str | None
) -> vol.Schema:
    def number(
        minimum: float, maximum: float, step: float = 1, **kwargs: Any
    ) -> NumberSelector:
        return NumberSelector(
            NumberSelectorConfig(
                min=minimum,
                max=maximum,
                step=step,
                mode=NumberSelectorMode.BOX,
                **kwargs,
            )
        )

    multiline = TextSelector(TextSelectorConfig(multiline=True))
    return vol.Schema(
        {
            vol.Required(CONF_NAME): TextSelector(),
            vol.Optional(CONF_MANUFACTURER_ID): TextSelector(),
            vol.Optional(CONF_PAYLOAD): TextSelector(),
            vol.Optional(CONF_LOCAL_NAME): TextSelector(),
            vol.Optional(CONF_SERVICE_UUIDS): TextSelector(
                TextSelectorConfig(multiple=True)
            ),
            vol.Optional(CONF_APPEARANCE): number(0, 0xFFFF),
            vol.Required(
                CONF_ADVERTISEMENT_TYPE, default=ADVERTISEMENT_TYPE_PERIPHERAL
            ): SelectSelector(
                SelectSelectorConfig(
                    options=ADVERTISEMENT_TYPES,
                    mode=SelectSelectorMode.DROPDOWN,
                    translation_key=CONF_ADVERTISEMENT_TYPE,
                )
            ),
            vol.Required(CONF_DURATION, default=DEFAULT_DURATION): NumberSelector(
                NumberSelectorConfig(
                    min=MIN_DURATION,
                    max=MAX_DURATION,
                    step=0.5,
                    mode=NumberSelectorMode.SLIDER,
                    unit_of_measurement="s",
                )
            ),
            vol.Required(CONF_ADAPTER, default=ADAPTER_DEFAULT): _adapter_selector(
                adapters, ADAPTER_DEFAULT, current_adapter
            ),
            vol.Required(SECTION_ADVANCED): section(
                vol.Schema(
                    {
                        vol.Optional(CONF_SERVICE_DATA): multiline,
                        vol.Optional(CONF_SOLICIT_UUIDS): TextSelector(
                            TextSelectorConfig(multiple=True)
                        ),
                        vol.Optional(
                            CONF_INCLUDE_TX_POWER, default=False
                        ): BooleanSelector(),
                        vol.Optional(CONF_TX_POWER): number(
                            MIN_TX_POWER, MAX_TX_POWER, unit_of_measurement="dBm"
                        ),
                        vol.Optional(
                            CONF_DISCOVERABLE, default=False
                        ): BooleanSelector(),
                        vol.Optional(CONF_MIN_INTERVAL): number(
                            MIN_INTERVAL_MS, MAX_INTERVAL_MS, unit_of_measurement="ms"
                        ),
                        vol.Optional(CONF_MAX_INTERVAL): number(
                            MIN_INTERVAL_MS, MAX_INTERVAL_MS, unit_of_measurement="ms"
                        ),
                        vol.Optional(CONF_DATA): multiline,
                    }
                ),
                {"collapsed": True},
            ),
        }
    )


def _form_to_signal(
    user_input: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, str]]:
    """Validate form input into saved-signal data, returning (data, errors)."""
    fields = {**user_input, **user_input.get(SECTION_ADVANCED, {})}
    errors: dict[str, str] = {}
    data: dict[str, Any] = {
        CONF_ADVERTISEMENT_TYPE: fields[CONF_ADVERTISEMENT_TYPE],
        CONF_DURATION: float(fields[CONF_DURATION]),
        CONF_ADAPTER: fields[CONF_ADAPTER].strip() or ADAPTER_DEFAULT,
    }

    def convert(
        key: str, validator: Any, error: str, error_field: str | None = None
    ) -> None:
        value = fields.get(key)
        if value is None or value == "" or value == []:
            return
        try:
            data[key] = validator(value)
        except vol.Invalid:
            errors[error_field or key] = error

    convert(CONF_MANUFACTURER_ID, uint16, "invalid_manufacturer_id")
    convert(CONF_PAYLOAD, normalize_hex, "invalid_hex")
    convert(CONF_SERVICE_UUIDS, uuid_list, "invalid_uuid")
    convert(CONF_APPEARANCE, uint16, "invalid_number")
    # Errors inside a collapsed section are reported on the whole form.
    convert(CONF_SERVICE_DATA, service_data_map, "invalid_service_data", "base")
    convert(CONF_SOLICIT_UUIDS, uuid_list, "invalid_solicit_uuids", "base")
    convert(CONF_TX_POWER, int, "invalid_number", "base")
    convert(CONF_MIN_INTERVAL, int, "invalid_number", "base")
    convert(CONF_MAX_INTERVAL, int, "invalid_number", "base")
    convert(CONF_DATA, ad_data_map, "invalid_data", "base")
    if fields.get(CONF_LOCAL_NAME, "").strip():
        data[CONF_LOCAL_NAME] = fields[CONF_LOCAL_NAME].strip()
    for key in (CONF_INCLUDE_TX_POWER, CONF_DISCOVERABLE):
        if fields.get(key):
            data[key] = True

    if (CONF_MANUFACTURER_ID in data) != (CONF_PAYLOAD in data) and not errors:
        errors[
            CONF_PAYLOAD if CONF_MANUFACTURER_ID in data else CONF_MANUFACTURER_ID
        ] = "manufacturer_data_incomplete"
    if data.get(CONF_MIN_INTERVAL, 0) > data.get(CONF_MAX_INTERVAL, MAX_INTERVAL_MS):
        errors["base"] = "invalid_interval"
    return data, errors


def _signal_to_form(title: str, data: Mapping[str, Any]) -> dict[str, Any]:
    """Return saved-signal data as suggested form values."""
    form: dict[str, Any] = {
        CONF_NAME: title,
        CONF_ADVERTISEMENT_TYPE: data[CONF_ADVERTISEMENT_TYPE],
        CONF_DURATION: data[CONF_DURATION],
        CONF_ADAPTER: data.get(CONF_ADAPTER, ADAPTER_DEFAULT),
    }
    advanced: dict[str, Any] = {
        CONF_INCLUDE_TX_POWER: data.get(CONF_INCLUDE_TX_POWER, False),
        CONF_DISCOVERABLE: data.get(CONF_DISCOVERABLE, False),
    }
    if CONF_MANUFACTURER_ID in data:
        form[CONF_MANUFACTURER_ID] = f"0x{int(data[CONF_MANUFACTURER_ID]):04x}"
    for key in (CONF_PAYLOAD, CONF_LOCAL_NAME, CONF_SERVICE_UUIDS, CONF_APPEARANCE):
        if key in data:
            form[key] = data[key]
    for key in (
        CONF_SOLICIT_UUIDS,
        CONF_TX_POWER,
        CONF_MIN_INTERVAL,
        CONF_MAX_INTERVAL,
    ):
        if key in data:
            advanced[key] = data[key]
    if CONF_SERVICE_DATA in data:
        advanced[CONF_SERVICE_DATA] = "\n".join(
            f"{uuid}={payload}" for uuid, payload in data[CONF_SERVICE_DATA].items()
        )
    if CONF_DATA in data:
        advanced[CONF_DATA] = "\n".join(
            f"0x{int(ad_type):02x}={payload}"
            for ad_type, payload in data[CONF_DATA].items()
        )
    form[SECTION_ADVANCED] = advanced
    return form


class SignalSubentryFlow(ConfigSubentryFlow):
    """Add or edit a saved signal."""

    _imported: Mapping[str, Any] | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Choose between pasting an advertisement and entering fields."""
        return self.async_show_menu(step_id="user", menu_options=["paste", "manual"])

    async def async_step_paste(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Paste an advertisement copied from the Advertisement Monitor."""
        errors: dict[str, str] = {}
        placeholders: dict[str, str] = {}
        if user_input is not None:
            try:
                name, config = parse_monitor_advertisement(
                    user_input[CONF_ADVERTISEMENT]
                )
            except AdvertisementImportError as err:
                errors["base"] = err.key
                placeholders = err.placeholders
            else:
                _LOGGER.debug("Imported advertisement %r: %s", name, config)
                self._imported = _signal_to_form(
                    name or "",
                    {
                        CONF_DURATION: DEFAULT_DURATION,
                        CONF_ADAPTER: ADAPTER_DEFAULT,
                        **config,
                    },
                )
                return await self.async_step_manual()
        return self.async_show_form(
            step_id="paste",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_ADVERTISEMENT): TextSelector(
                        TextSelectorConfig(multiline=True)
                    )
                }
            ),
            errors=errors,
            description_placeholders={"ids": "", **placeholders},
        )

    async def async_step_manual(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Add a signal, pre-filled when an advertisement was pasted."""
        return await self._async_step_signal("manual", user_input)

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Edit a signal."""
        return await self._async_step_signal("reconfigure", user_input)

    async def _async_step_signal(
        self, step_id: str, user_input: dict[str, Any] | None
    ) -> SubentryFlowResult:
        entry = self._get_entry()
        subentry = (
            self._get_reconfigure_subentry() if step_id == "reconfigure" else None
        )
        errors: dict[str, str] = {}

        if user_input is not None:
            data, errors = _form_to_signal(user_input)
            if not errors:
                title = user_input[CONF_NAME].strip()
                if subentry is not None:
                    return self.async_update_and_abort(
                        entry, subentry, title=title, data=data
                    )
                return self.async_create_entry(title=title, data=data)
            suggested: Mapping[str, Any] = user_input
        elif subentry is not None:
            suggested = _signal_to_form(subentry.title, subentry.data)
        else:
            suggested = self._imported or {}

        schema = _signal_schema(
            await _async_discover(entry), suggested.get(CONF_ADAPTER)
        )
        return self.async_show_form(
            step_id=step_id,
            data_schema=self.add_suggested_values_to_schema(schema, suggested),
            errors=errors,
        )
