"""pyhatchbabyrestasync.

Derived from kjoconnor's pyhatchbabyrest repo.
All rights reserved.
https://github.com/kjoconnor/pyhatchbabyrest/blob/master/LICENSE

"""

from collections.abc import Callable
import asyncio
from datetime import datetime
import logging
from time import monotonic

from bleak.backends.device import BLEDevice
from bleak_retry_connector import (
    BleakAbortedError,
    BleakClientWithServiceCache,
    BleakConnectionError,
    BleakNotFoundError,
    BleakOutOfConnectionSlotsError,
    establish_connection,
)

from .const import CHAR_FEEDBACK, CHAR_TX, PyHatchBabyRestSound

_LOGGER = logging.getLogger(__name__)


def _assert_value(check_val: list[str], index: int, assert_val: str):
    if check_val[index] != assert_val:
        raise ValueError(f'response[{index}] "{check_val[index]}" != "{assert_val}"')


class PyHatchBabyRestAsync:
    """An asynchronous interface to a Hatch Rest device using bleak."""

    def __init__(self, ble_device: BLEDevice) -> None:
        """Init PyHatchBabyRestAsync."""
        self.device = ble_device
        self.address = ble_device.address

        self._client: BleakClientWithServiceCache | None = None
        self._active_operations: int = 0

        # connection synchronization primitizes / state
        self._connection_cv = asyncio.Condition()
        self._connecting: bool = False
        self._disconnect_timer: asyncio.TimerHandle | None = None
        self._callbacks: set[Callable[[], None]] = set()
        self._is_notifying: bool = False

        # cached device state
        self.color: tuple[int, int, int] | None = None
        self.brightness: int | None = None
        self.sound: PyHatchBabyRestSound | None = None
        self.volume: int | None = None
        self.power: bool | None = None

    def update_ble_device(self, ble_device: BLEDevice) -> None:
        """Update the BLE device."""
        self.device = ble_device
        self.address = ble_device.address

    def update_from_advertisement(self, service_info: "BluetoothServiceInfoBleak") -> None:
        """Update state from advertisement data."""
        from .const import MANUFACTURER_ID

        if MANUFACTURER_ID not in service_info.manufacturer_data:
            return

        data = service_info.manufacturer_data[MANUFACTURER_ID]
        _LOGGER.debug("Received advertisement data: %s", data.hex())

        # The advertisement data format for Hatch Rest varies, but some versions
        # include status bytes. If yours does, we can parse it here.
        # For now, we update the BLEDevice so the next connection is faster.
        self.update_ble_device(service_info.device)

    def register_callback(self, callback: Callable[[], None]) -> None:
        """Register a callback for when data is updated."""
        self._callbacks.add(callback)

    def remove_callback(self, callback: Callable[[], None]) -> None:
        """Remove a previously registered callback."""
        self._callbacks.discard(callback)

    def _notify_callbacks(self) -> None:
        """Notify all registered callbacks of an update."""
        for callback in self._callbacks:
            callback()

    def _set_active_operations(self, amount: int):
        """Change the number of running tasks."""
        if amount > 0:
            _LOGGER.debug("Incrementing self._active_operations by %d", amount)
            self._active_operations += 1
        if amount < 0:
            _LOGGER.debug("Decrementing self._active_operations by %d", abs(amount))
            self._active_operations -= 1
        _LOGGER.debug("self._active_operations = %d", self._active_operations)

    def _client_disconnected(self, client: BleakClientWithServiceCache) -> None:
        """Callback for when the client disconnects."""
        _LOGGER.debug("API client has successfully disconnected")
        self._client = None
        self._is_notifying = False
        if self._disconnect_timer:
            self._disconnect_timer.cancel()
            self._disconnect_timer = None

    async def _client_connect(self) -> None:
        """Connect to the device."""
        if self._disconnect_timer:
            self._disconnect_timer.cancel()
            self._disconnect_timer = None

        async with self._connection_cv:
            if self._client and self._client.is_connected:
                _LOGGER.debug(
                    "self._client = %s and and self._client.is_connected = %s -- using existing connection",
                    self._client,
                    self._client.is_connected,
                )
                return

            if self._connecting:
                _LOGGER.debug(
                    "self._connecting = %s -- wait for connection to establish",
                    self._connecting,
                )
                await self._connection_cv.wait()
                return

            _LOGGER.debug("No existing connection -- setting self._connecting = True")
            self._connecting = True

        try:
            client = await establish_connection(
                BleakClientWithServiceCache,
                self.device,
                self.address,
                disconnected_callback=self._client_disconnected,
            )
            _LOGGER.debug("Client connected: %s", client.is_connected)

            # Start notifications if supported
            if client.is_connected and not self._is_notifying:
                try:
                    _LOGGER.debug("Starting notifications on %s", CHAR_FEEDBACK)
                    await client.start_notify(CHAR_FEEDBACK, self._notification_handler)
                    self._is_notifying = True
                except Exception as e:
                    _LOGGER.warning("Could not start notifications: %r", e)

        except (
            BleakNotFoundError,
            BleakOutOfConnectionSlotsError,
            BleakAbortedError,
            BleakConnectionError,
            Exception,  # noqa: BLE001
        ) as e:
            _LOGGER.warning("Exception during _client_connect -- %r", e)
            client = None

        async with self._connection_cv:
            self._connecting = False
            self._client = client
            self._connection_cv.notify_all()

    def _schedule_disconnect(self) -> None:
        """Schedule a disconnection after a cooldown period."""
        if self._disconnect_timer:
            self._disconnect_timer.cancel()

        self._disconnect_timer = asyncio.get_event_loop().call_later(
            10, lambda: asyncio.create_task(self._client_disconnect())
        )
        _LOGGER.debug("Scheduled disconnect in 10 seconds")

    async def _client_disconnect(self) -> None:
        """Disconnect from the device."""
        if self._disconnect_timer:
            self._disconnect_timer.cancel()
            self._disconnect_timer = None

        if self._client and self._active_operations == 0:
            _LOGGER.debug(
                "self._client = %s and self._running_tasks = %d, attempting to disconnect",
                self._client,
                self._active_operations,
            )
            try:
                await self._client.disconnect()

            except (
                BleakNotFoundError,
                BleakOutOfConnectionSlotsError,
                BleakAbortedError,
                BleakConnectionError,
                Exception,  # noqa: BLE001
            ) as e:
                _LOGGER.warning("Exception during _client_disconnect -- %r", e)
        else:
            _LOGGER.debug(
                "self._client = %s and self._running_tasks = %d, cannot currently disconnect",
                self._client,
                self._active_operations,
            )

    async def _send_command(self, command: str):
        """Send a command do the device.

        :param command: The command to send.
        """
        if log_timing := _LOGGER.isEnabledFor(logging.DEBUG):
            start = monotonic()
            _LOGGER.debug("Started _send_command at %s", datetime.now().isoformat())

        self._set_active_operations(1)
        await self._client_connect()

        try:
            if self._client and self._client.is_connected:
                await self._client.write_gatt_char(
                    char_specifier=CHAR_TX,
                    data=bytearray(command, "utf-8"),
                    response=True,
                )
            else:
                _LOGGER.warning("Could not send command: Not connected to device")

        except (
            BleakNotFoundError,
            BleakOutOfConnectionSlotsError,
            BleakAbortedError,
            BleakConnectionError,
            Exception,  # noqa: BLE001
        ) as e:
            _LOGGER.warning("Exception during _send_command -- %r", e)

        self._set_active_operations(-1)
        self._schedule_disconnect()

        if log_timing:
            _LOGGER.debug(
                "Finished _send_command at %s (total of %.3f seconds)",
                datetime.now().isoformat(),
                monotonic() - start,  # pyright: ignore[reportPossiblyUnboundVariable]
            )

    def _notification_handler(self, characteristic: int, data: bytearray) -> None:
        """Handle incoming notifications from the device."""
        # _parse_data already handles the logic and "CHANGED" logging
        self._parse_data(data)
        self._notify_callbacks()

    def _parse_data(self, data: bytearray) -> None:
        """Parse raw device data and update state."""
        response = [hex(x) for x in data]

        # Make sure the data is where we think it is
        try:
            _assert_value(response, 5, "0x43")  # color
            _assert_value(response, 10, "0x53")  # audio
            _assert_value(response, 13, "0x50")  # power

            red, green, blue, brightness = [int(x, 16) for x in response[6:10]]
            sound = PyHatchBabyRestSound(int(response[11], 16))
            volume = int(response[12], 16)
            power = not bool(int("11000000", 2) & int(response[14], 16))

            new_color = (red, green, blue)

            # Check if anything has actually changed
            if (
                new_color == self.color
                and brightness == self.brightness
                and sound == self.sound
                and volume == self.volume
                and power == self.power
            ):
                _LOGGER.debug("State unchanged, skipping callback")
                return

            self.color = new_color
            self.brightness = brightness
            self.sound = sound
            self.volume = volume
            self.power = power

            _LOGGER.debug(
                "Parsed state (CHANGED): power=%s, brightness=%s, color=%s, sound=%s, volume=%s",
                self.power,
                self.brightness,
                self.color,
                self.sound,
                self.volume,
            )
            self._notify_callbacks()
        except (ValueError, IndexError) as e:
            _LOGGER.warning("Failed to parse device data: %r", e)

    async def refresh_data(self):
        """Refresh data from Hatch Rest device."""
        if log_timing := _LOGGER.isEnabledFor(logging.DEBUG):
            start = monotonic()
            _LOGGER.debug("Started refresh_data at %s", datetime.now().isoformat())

        self._set_active_operations(1)
        await self._client_connect()

        try:
            if self._client and self._client.is_connected:
                raw_char_read = await self._client.read_gatt_char(CHAR_FEEDBACK)
                _LOGGER.debug("Raw char read from refresh_data: %s", raw_char_read)
                self._parse_data(raw_char_read)
            else:
                _LOGGER.warning("Could not refresh data: Not connected to device")

        except (
            BleakNotFoundError,
            BleakOutOfConnectionSlotsError,
            BleakAbortedError,
            BleakConnectionError,
            Exception,  # noqa: BLE001
        ) as e:
            _LOGGER.warning("Exception during refresh_data -- %r", e)

        self._set_active_operations(-1)
        self._schedule_disconnect()

        if log_timing:
            _LOGGER.debug(
                "Finished refresh_data at %s (total of %.3f seconds)",
                datetime.now().isoformat(),
                monotonic() - start,  # pyright: ignore[reportPossiblyUnboundVariable]
            )


    async def turn_power_on(self):
        """Power on the Hatch Rest device."""
        command = f"SI{1:02x}"
        _LOGGER.debug("API command: turn_power_on")
        self.power = True
        await self._send_command(command)

    async def turn_power_off(self):
        """Power off the Hatch Rest device."""
        command = f"SI{0:02x}"
        _LOGGER.debug("API command: turn_power_off")
        self.power = False
        await self._send_command(command)

    async def set_sound(self, sound: int):
        """Set the sound of the Hatch Rest device."""
        command = f"SN{sound:02x}"
        _LOGGER.debug("API command: set_sound to %s", command)
        self.sound = PyHatchBabyRestSound(sound)
        return await self._send_command(command)

    async def set_volume(self, volume: int):
        """Set the volume of the Hatch Rest device."""
        command = f"SV{volume:02x}"
        _LOGGER.debug("API command: set_volume to %s", command)
        self.volume = volume
        return await self._send_command(command)

    async def set_color(self, red: int, green: int, blue: int):
        """Set the color of the Hatch Rest device."""
        return await self.set_light_state(color=(red, green, blue))

    async def set_brightness(self, brightness: int):
        """Set the brightness of the Hatch Rest device."""
        return await self.set_light_state(brightness=brightness)

    async def set_light_state(
        self,
        brightness: int | None = None,
        color: tuple[int, int, int] | None = None,
    ):
        """Set the light state (color and brightness) in one command."""
        if brightness is not None:
            self.brightness = brightness
        if color is not None:
            self.color = color

        if self.color is not None and self.brightness is not None:
            command = f"SC{self.color[0]:02x}{self.color[1]:02x}{self.color[2]:02x}{self.brightness:02x}"
            _LOGGER.debug("API command: set_light_state to %s", command)
            return await self._send_command(command)
        return None

    @property
    def name(self):
        """Return the name of the Hatch Rest device."""
        return self.device.name
