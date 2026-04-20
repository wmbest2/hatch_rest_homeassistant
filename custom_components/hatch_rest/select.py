"""Hatch Rest select."""

import logging

from homeassistant.components.select import SelectEntity
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
    """Set up Hatch Rest select."""
    coordinator = config_entry.runtime_data
    async_add_entities([HatchBabyRestFavoriteSelect(coordinator)])


class HatchBabyRestFavoriteSelect(HatchBabyRestEntity, SelectEntity):
    """Hatch Rest favorite select entity."""

    @property
    def name(self) -> str | None:
        """Return the name of the entity."""
        if self._hatch_rest_device.name:
            return f"{self._hatch_rest_device.name.title()} Favorite"
        return None

    @property
    def unique_id(self) -> str | None:
        """Return a unique ID."""
        return f"{self._attr_unique_id}_favorite"

    @property
    def options(self) -> list[str]:
        """Return a list of available favorites."""
        options = ["None"]
        for i in range(1, 7):
            fav_info = self._hatch_rest_device.favorites.get(i, {})
            options.append(fav_info.get("name", f"Favorite {i}"))
        return options

    @property
    def current_option(self) -> str | None:
        """Return the current selected favorite."""
        active_fav = self._hatch_rest_device.active_favorite
        if not active_fav or active_fav > 6:
            return "None"
        
        fav_info = self._hatch_rest_device.favorites.get(active_fav, {})
        return fav_info.get("name", f"Favorite {active_fav}")

    async def async_select_option(self, option: str) -> None:
        """Select a favorite."""
        if option == "None":
            # There isn't a direct "deselect favorite" other than setting a manual state.
            # But SP00 might work, or we just leave it.
            return

        selected_index = None
        for i in range(1, 7):
            fav_info = self._hatch_rest_device.favorites.get(i, {})
            if option == fav_info.get("name") or option == f"Favorite {i}":
                selected_index = i
                break
        
        if selected_index is not None:
            await self._hatch_rest_device.select_favorite(selected_index)
            await self.coordinator.async_refresh()
