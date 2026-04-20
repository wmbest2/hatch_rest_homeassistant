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
        """Return a list of enabled favorites."""
        options = ["None"]
        for i in range(1, 7):
            fav_info = self._hatch_rest_device.favorites.get(i, {})
            # Only include if explicitly enabled or if we don't have the info yet (to avoid empty list)
            if fav_info.get("enabled", True):
                options.append(fav_info.get("name", f"Favorite {i}"))
        return options

    @property
    def current_option(self) -> str | None:
        """Return the current selected favorite."""
        active_fav = self._hatch_rest_device.active_favorite
        if active_fav is None or active_fav == 0 or active_fav > 6:
            return "None"
        
        fav_info = self._hatch_rest_device.favorites.get(active_fav, {})
        name = fav_info.get("name", f"Favorite {active_fav}")
        return name if name in self.options else "None"

    async def async_select_option(self, option: str) -> None:
        """Select a favorite."""
        if option == "None":
            # Deselecting a favorite usually means turning the device off or to a manual state.
            # SP00 (None) is sometimes used in these protocols.
            await self._hatch_rest_device.select_favorite(0)
            await self.coordinator.async_refresh()
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
