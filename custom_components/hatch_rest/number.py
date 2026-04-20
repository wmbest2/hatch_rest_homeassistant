"""Hatch Rest number."""

import logging

from homeassistant.components.number import NumberEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import HatchBabyRestEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Hatch Rest number."""
    coordinator = config_entry.runtime_data
    async_add_entities([HatchBabyRestTimerNumber(coordinator)])


class HatchBabyRestTimerNumber(HatchBabyRestEntity, NumberEntity):
    """Hatch Rest timer number — shows and sets timer in minutes; device uses seconds."""

    _attr_native_min_value = 0
    _attr_native_max_value = 120
    _attr_native_step = 1

    @property
    def name(self) -> str | None:
        """Return the name of the entity."""
        if self._hatch_rest_device.name:
            return f"{self._hatch_rest_device.name.title()} Set Timer"
        return None

    @property
    def unique_id(self) -> str | None:
        """Return a unique ID."""
        return f"{self._attr_unique_id}_set_timer"

    @property
    def native_value(self) -> float | None:
        """Return remaining timer in minutes, or None if no timer is active."""
        remaining = self.coordinator.data.get("timer_remaining")
        return remaining if remaining else None

    async def async_set_native_value(self, value: float) -> None:
        """Set the timer in minutes."""
        await self._hatch_rest_device.set_timer(int(value) * 60)
        self.coordinator.async_set_updated_data(self.coordinator.get_current_data())
