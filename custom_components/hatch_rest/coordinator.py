"""Hatch Rest coordinator."""

from datetime import datetime, timedelta
import logging

from homeassistant.components import bluetooth
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
)

from .api import PyHatchBabyRestAsync
from .const import DOMAIN, PyHatchBabyRestSound

_LOGGER = logging.getLogger(__name__)


class HatchBabyRestUpdateCoordinator(DataUpdateCoordinator):
    """Hatch Rest data update coordinator."""

    def __init__(
        self,
        hass: HomeAssistant,
        unique_id: str | None,
        hatch_rest_device: PyHatchBabyRestAsync,
        update_interval: timedelta,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=update_interval,
        )
        self.unique_id = unique_id
        self.hatch_rest_device = hatch_rest_device
        self._last_data: dict[
            str, int | tuple[int, int, int] | bool | PyHatchBabyRestSound | None
        ] = {}

        # Register callback for real-time updates from connections
        self.hatch_rest_device.register_callback(self._handle_api_update)

        # Register callback for passive advertisements
        self._address = self.hatch_rest_device.address.upper()
        self._cancel_bluetooth_advertisements = bluetooth.async_register_callback(
            self.hass,
            self._handle_advertisement,
            {"address": self._address, "connectable": True},
            bluetooth.BluetoothScanningMode.PASSIVE,
        )


    @callback
    def _handle_advertisement(
        self,
        service_info: bluetooth.BluetoothServiceInfoBleak,
        change: bluetooth.BluetoothChange,
    ) -> None:
        """Handle an advertisement from the device."""
        _LOGGER.debug("Received advertisement for %s", service_info.address)
        self.hatch_rest_device.update_from_advertisement(service_info)
        # Note: update_from_advertisement will trigger the API callback if state changed

    def _handle_api_update(self) -> None:
        """Handle pushed updates from the API."""
        _LOGGER.debug("API pushed an update, updating coordinator data")
        self.async_set_updated_data(self.get_current_data())

    def get_current_data(
        self,
    ) -> dict[str, int | tuple[int, int, int] | bool | PyHatchBabyRestSound | None | datetime]:
        """Get the current state of the Hatch Rest device."""
        timer_expires_at = self.hatch_rest_device._timer_expires_at
        timer_end_time = None
        if timer_expires_at is not None:
            # Convert monotonic time to UTC datetime
            from homeassistant.util import dt as dt_util
            from time import monotonic
            
            remaining = timer_expires_at - monotonic()
            timer_end_time = dt_util.utcnow() + timedelta(seconds=remaining)

        data: dict[
            str, int | tuple[int, int, int] | bool | PyHatchBabyRestSound | None | datetime
        ] = {
            "brightness": self.hatch_rest_device.brightness,
            "color": self.hatch_rest_device.color,
            "power": self.hatch_rest_device.power,
            "sound": self.hatch_rest_device.sound,
            "volume": self.hatch_rest_device.volume,
            "timer_total": self.hatch_rest_device.timer_total,
            "timer_remaining": self.hatch_rest_device.timer_remaining,
            "timer_end_time": timer_end_time,
        }
        _LOGGER.debug("Data updated: %s", data)
        return data

    async def _async_update_data(
        self,
    ) -> dict[str, int | tuple[int, int, int] | bool | PyHatchBabyRestSound | None]:
        _LOGGER.debug("Starting coordinator async update")
        self._last_data = self.data if self.data else {}
        try:
            await self.hatch_rest_device.refresh_data()
        except Exception as e:
            _LOGGER.warning("_async_update_data failed: %r", e)
            if self._last_data:
                return self._last_data
            from homeassistant.helpers.update_coordinator import UpdateFailed
            raise UpdateFailed(f"Device update failed: {e}") from e
        return self.get_current_data()


class HatchBabyRestEntity(CoordinatorEntity[HatchBabyRestUpdateCoordinator]):
    """Hatch Rest entity."""

    def __init__(self, coordinator: HatchBabyRestUpdateCoordinator) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        self._hatch_rest_device = coordinator.hatch_rest_device
        self._attr_unique_id = coordinator.unique_id

    @property
    def device_info(self) -> DeviceInfo:  # pyright: ignore[reportIncompatibleVariableOverride]
        """Return device specific attributes."""
        if not all((self._hatch_rest_device.address, self.unique_id)):
            raise ValueError("Missing bluetooth address for hatch rest device")

        assert self._hatch_rest_device.address
        assert self.unique_id

        return DeviceInfo(
            connections={(dr.CONNECTION_BLUETOOTH, self._hatch_rest_device.address)},
            identifiers={(DOMAIN, self.unique_id)},
            manufacturer="Hatch",
            model="Rest",
            name=self.device_name,
        )

    @property
    def device_name(self):
        """Return the name of the device."""
        return self._hatch_rest_device.name
