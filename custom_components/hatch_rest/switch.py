"""Hatch Rest switch."""

import logging

import voluptuous as vol

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv, entity_platform
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import HatchBabyRestEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Hatch Rest switch."""
    coordinator = config_entry.runtime_data
    # only need to update_before_add on one entity -- switch is "master" entity
    async_add_entities([HatchBabyRestSwitch(coordinator)], update_before_add=True)

    platform = entity_platform.async_get_current_platform()
    platform.async_register_entity_service(
        "select_favorite",
        {vol.Required("index"): vol.All(cv.positive_int, vol.Range(min=1, max=6))},
        "async_select_favorite",
    )
    platform.async_register_entity_service(
        "send_command",
        {
            vol.Required("command"): cv.string,
            vol.Optional("raw", default=False): cv.boolean,
        },
        "async_send_command",
    )


class HatchBabyRestSwitch(HatchBabyRestEntity, SwitchEntity):  # pyright: ignore[reportIncompatibleVariableOverride]
    """Hatch Rest switch entity."""

    @property
    def is_on(self) -> bool | None:  # pyright: ignore[reportIncompatibleVariableOverride]
        """Return whether the switch is on or not."""
        _LOGGER.debug("switch is_on = %s", self.coordinator.data.get("power"))
        return self.coordinator.data.get("power")

    @property
    def name(self) -> str | None:  # pyright: ignore[reportIncompatibleVariableOverride]
        """Return the name of the entity."""
        if self._hatch_rest_device.name:
            return f"{self._hatch_rest_device.name.title()} Switch"
        return None

    async def async_turn_on(self, **_):
        """Turn on the Hatch Rest device."""
        if not self.is_on:
            _LOGGER.debug("switch setting on")
            await self._hatch_rest_device.turn_power_on()

            # https://developers.home-assistant.io/docs/integration_fetching_data/
            # If this method is used on a coordinator that polls, it will reset the time until the next time it will poll for data.
            # each _send_command calls _refresh_data and updates API data states, so use that
            self.coordinator.async_set_updated_data(self.coordinator.get_current_data())

    async def async_turn_off(self, **_):
        """Turn off the Hatch Rest device."""
        if self.is_on:
            _LOGGER.debug("switch setting off")
            await self._hatch_rest_device.turn_power_off()

            # https://developers.home-assistant.io/docs/integration_fetching_data/
            # If this method is used on a coordinator that polls, it will reset the time until the next time it will poll for data.
            # each _send_command calls _refresh_data and updates API data states, so use that
            self.coordinator.async_set_updated_data(self.coordinator.get_current_data())

    async def async_select_favorite(self, index: int) -> None:
        """Select a favorite slot by index."""
        _LOGGER.debug("Service: select_favorite index=%d", index)
        await self._hatch_rest_device.select_favorite(index)
        await self.coordinator.async_refresh()

    async def async_send_command(self, command: str, raw: bool = False) -> None:
        """Send a command to the device."""
        _LOGGER.debug("Service: send_command '%s' raw=%s", command, raw)
        await self._hatch_rest_device._send_command(command, raw=raw)
