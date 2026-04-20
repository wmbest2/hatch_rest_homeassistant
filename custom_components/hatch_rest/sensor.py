"""Hatch Rest sensor."""

from datetime import datetime
import logging

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
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
    """Set up Hatch Rest sensor."""
    coordinator = config_entry.runtime_data
    async_add_entities([HatchBabyRestTimerSensor(coordinator)])


class HatchBabyRestTimerSensor(HatchBabyRestEntity, SensorEntity):
    """Hatch Rest timer sensor."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP

    @property
    def name(self) -> str | None:
        """Return the name of the entity."""
        if self._hatch_rest_device.name:
            return f"{self._hatch_rest_device.name.title()} Timer Remaining"
        return None

    @property
    def unique_id(self) -> str | None:
        """Return a unique ID."""
        return f"{self._attr_unique_id}_timer_remaining"

    @property
    def native_value(self) -> datetime | None:
        """Return the state of the sensor."""
        return self.coordinator.data.get("timer_end_time")
