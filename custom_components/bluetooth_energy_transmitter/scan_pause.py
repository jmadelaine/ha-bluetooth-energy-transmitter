"""Pause Home Assistant's Bluetooth scanning on an adapter while it advertises.

A Bluetooth radio can't transmit and receive at the same moment. Home
Assistant's Bluetooth integration keeps the adapter scanning, so the controller
has to fit our advertising in around the scan and can skip many advertising
events. A device that only listens briefly in standby may then miss us.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
import logging
from typing import Any

from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

# Give the controller a moment to leave scanning before we start advertising.
SETTLE_DELAY = 0.25


class ScanPauser:
    """Stop the scanner for an adapter while at least one broadcast uses it."""

    def __init__(
        self,
        hass: HomeAssistant,
        scanner_lookup: Callable[[HomeAssistant, str], Any] | None = None,
    ) -> None:
        """Initialize; `scanner_lookup` defaults to bluetooth.async_scanner_by_source."""
        self._hass = hass
        self._scanner_lookup = scanner_lookup
        self._lock = asyncio.Lock()
        self._users: dict[str, int] = {}
        self._stopped: dict[str, Any] = {}

    @asynccontextmanager
    async def async_pause(self, address: str | None) -> AsyncIterator[None]:
        """Pause scanning on the adapter with this MAC address."""
        if address is None:
            yield
            return
        await self._async_acquire(address)
        try:
            yield
        finally:
            await self._async_release(address)

    def _scanner(self, address: str) -> Any:
        if "bluetooth" not in self._hass.config.components:
            return None
        try:
            if (lookup := self._scanner_lookup) is None:
                from homeassistant.components.bluetooth import (
                    async_scanner_by_source as lookup,
                )
            scanner = lookup(self._hass, address)
        except Exception as err:
            _LOGGER.debug("Could not look up the scanner for %s: %s", address, err)
            return None
        # Only the local scanner can be stopped; remote proxies don't share
        # our radio anyway.
        if not (hasattr(scanner, "async_stop") and hasattr(scanner, "async_start")):
            return None
        return scanner

    async def _async_acquire(self, address: str) -> None:
        async with self._lock:
            self._users[address] = self._users.get(address, 0) + 1
            if self._users[address] > 1:
                return
            scanner = self._scanner(address)
            if scanner is None or not getattr(scanner, "scanning", False):
                return
            _LOGGER.debug("Pausing Home Assistant Bluetooth scanning on %s", address)
            try:
                await scanner.async_stop()
            except Exception as err:
                _LOGGER.warning("Could not pause scanning on %s: %s", address, err)
                return
            self._stopped[address] = scanner
        await asyncio.sleep(SETTLE_DELAY)

    async def _async_release(self, address: str) -> None:
        async with self._lock:
            self._users[address] -= 1
            if self._users[address]:
                return
            del self._users[address]
            if (scanner := self._stopped.pop(address, None)) is None:
                return
            _LOGGER.debug("Resuming Home Assistant Bluetooth scanning on %s", address)
            try:
                await scanner.async_start()
            except Exception as err:
                _LOGGER.warning(
                    "Could not resume Bluetooth scanning on %s: %s. Reload the "
                    "Bluetooth integration if Bluetooth devices stop updating",
                    address,
                    err,
                )
