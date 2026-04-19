"""pyhatchbabyrestasync.

Derived from kjoconnor's pyhatchbabyrest repo.
All rights reserved.
https://github.com/kjoconnor/pyhatchbabyrest/blob/master/LICENSE

"""

from collections.abc import Callable
import asyncio
from datetime import datetime
import logging
import struct
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

from .const import CHAR_FEEDBACK, CHAR_LIST, CHAR_TX, PyHatchBabyRestSound

_LOGGER = logging.getLogger(__name__)


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
        
        self.favorites: dict[int, dict] = {}
        self.active_favorite: int | None = None
        self.schedules: dict[int, dict] = {}
        self._last_index_seen: int | None = None
        self._pending_slot: dict = {}  # buffered data before first index arrives
        self._pgb_slot: int | None = None  # slot index of in-flight PGB (favorite) request
        self._egb_slot: int | None = None  # slot index of in-flight EGB (schedule) request
        self._last_full_fetch: float | None = None  # monotonic time of last PGB+EGB sweep

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

        # Update the BLEDevice so the next connection is faster
        self.update_ble_device(service_info.device)

        # Parse the advertisement data (it uses the same tagged format)
        if self._parse_data(data):
            self._notify_callbacks()

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
                return

            if self._connecting:
                await self._connection_cv.wait()
                return

            self._connecting = True

        try:
            client = await establish_connection(
                BleakClientWithServiceCache,
                self.device,
                self.address,
                disconnected_callback=self._client_disconnected,
            )
            _LOGGER.debug("Client connected: %s", client.is_connected)

            # Reset per-connection parse state
            self._last_index_seen = None
            self._pending_slot = {}
            self._pgb_slot = None
            self._egb_slot = None

            # Start notifications
            if client.is_connected:
                try:
                    # We subscribe to the CONFIG channel FIRST to ensure we don't miss the dump
                    _LOGGER.debug("Subscribing to config channel %s...", CHAR_LIST)
                    await client.start_notify(CHAR_LIST, self._notification_handler)
                    
                    _LOGGER.debug("Subscribing to feedback channel %s...", CHAR_FEEDBACK)
                    await client.start_notify(CHAR_FEEDBACK, self._notification_handler)
                    
                    self._is_notifying = True
                    # GF returns the active favorite index. Then PGB01-PGB06
                    # fetches each slot's config+name. Both confirmed via btsnoop.
                    _LOGGER.debug("Requesting active favorite index via 'GF'")
                    await client.write_gatt_char(
                        CHAR_TX, bytearray(b"GF"), response=False
                    )
                    fetch_age = monotonic() - self._last_full_fetch if self._last_full_fetch else None
                    if fetch_age is None or fetch_age > 3600:
                        _LOGGER.debug("Fetching favorites and schedules (age=%s)", fetch_age)
                        self.favorites = {}
                        self.schedules = {}

                        for slot in range(1, 7):
                            self._pgb_slot = slot
                            cmd = f"PGB{slot:02X}".encode()
                            _LOGGER.debug("Requesting favorite slot '%s'", cmd.decode())
                            await client.write_gatt_char(CHAR_TX, bytearray(cmd), response=False)
                            await asyncio.sleep(0.15)
                        self._pgb_slot = None

                        for slot in range(1, 11):
                            self._egb_slot = slot
                            cmd = f"EGB{slot:02X}".encode()
                            _LOGGER.debug("Requesting schedule slot '%s'", cmd.decode())
                            await client.write_gatt_char(CHAR_TX, bytearray(cmd), response=False)
                            await asyncio.sleep(0.15)
                        self._egb_slot = None

                        self._last_full_fetch = monotonic()
                    else:
                        _LOGGER.debug("Skipping favorites/schedules fetch (age=%.0fs)", fetch_age)
                except Exception as e:
                    if "already notifying" not in str(e):
                        _LOGGER.warning("Notification error: %r", e)

        except Exception as e:
            _LOGGER.warning("Connect error: %r", e)
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
                "self._client = %s and self._active_operations = %d, attempting to disconnect",
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
                "self._client = %s and self._active_operations = %d, cannot currently disconnect",
                self._client,
                self._active_operations,
            )

    async def _send_command(self, command: str, raw: bool = False):
        """Send a command to the device.

        :param command: ASCII command string, or hex-encoded bytes if raw=True.
        :param raw: If True, decode command as hex string and send raw bytes.
        """
        if log_timing := _LOGGER.isEnabledFor(logging.DEBUG):
            start = monotonic()
            _LOGGER.debug("Started _send_command at %s", datetime.now().isoformat())

        self._set_active_operations(1)
        await self._client_connect()

        try:
            if self._client and self._client.is_connected:
                data = bytearray.fromhex(command) if raw else bytearray(command, "utf-8")
                _LOGGER.debug("Sending command '%s' (Hex: %s)", command, data.hex())
                await self._client.write_gatt_char(
                    char_specifier=CHAR_TX,
                    data=data,
                    response=False,
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

    def _notification_handler(self, char_specifier, data: bytearray) -> None:
        """Handle incoming notifications from the device."""
        # char_specifier can be a BleakGATTCharacteristic (has .uuid) or an int handle
        char_uuid = getattr(char_specifier, "uuid", None)
        handle = char_specifier if isinstance(char_specifier, int) else getattr(char_specifier, "handle", None)
        
        _LOGGER.debug("[Notification] Handle: %s, UUID: %s, Data: %s", handle, char_uuid, data.hex())
        
        # Identify the source. 02240003 is often handle 19, 02260002 is handle 23.
        # We check UUID string first, then fallback to logic based on characteristic type
        is_config = False
        if char_uuid == CHAR_LIST:
            is_config = True
        elif isinstance(char_specifier, int) or char_uuid is None:
            # Fallback for handle-only notifications: config packets usually 
            # don't start with 'Ti' (0x54 0x69)
            if not data.startswith(b"Ti"):
                is_config = True

        if is_config:
            self._parse_config_data(data)
            self._notify_callbacks()
        elif self._parse_data(data):
            self._notify_callbacks()

    @staticmethod
    def _decode_config_block(data: bytearray) -> dict:
        """Decode a 15-byte PGB favorite block.

        Layout: [0x01] [sound] [volume] [00x6] [brightness] [B] [G] [R] [flags] [0x03]
        """
        sound_id = data[1]
        volume = data[2]
        brightness = data[9]
        blue, green, red = data[10], data[11], data[12]
        try:
            sound = PyHatchBabyRestSound(sound_id)
        except ValueError:
            sound = sound_id
        return {
            "sound": sound,
            "volume": volume,
            "brightness": brightness,
            "color": (red, green, blue),
            "raw": data.hex(),
        }

    @staticmethod
    def _decode_schedule_block(data: bytearray) -> dict:
        """Decode a 20-byte EGB schedule block.

        Layout (confirmed via live EGB responses):
          [0x01] [unix_ts LE x4] [sound] [volume] [hour] [minute]
          [00x4] [brightness] [B] [G] [R] [0x00] [days_bitmask] [flags]

        days_bitmask bits 1-5 = Mon-Fri (0x3e = Mon-Fri confirmed).
        """
        sound_id = data[5]
        volume = data[6]
        hour = data[7]
        minute = data[8]
        brightness = data[13]
        blue, green, red = data[14], data[15], data[16]
        days_mask = data[18] if len(data) > 18 else 0
        modified_ts = struct.unpack_from("<I", data, 1)[0]
        days = {
            "mon": bool(days_mask & 0x02),
            "tue": bool(days_mask & 0x04),
            "wed": bool(days_mask & 0x08),
            "thu": bool(days_mask & 0x10),
            "fri": bool(days_mask & 0x20),
            "sat": bool(days_mask & 0x40),
            "sun": bool(days_mask & 0x01),
        }
        try:
            sound = PyHatchBabyRestSound(sound_id)
        except ValueError:
            sound = sound_id
        return {
            "sound": sound,
            "volume": volume,
            "hour": hour,
            "minute": minute,
            "brightness": brightness,
            "color": (red, green, blue),
            "days": days,
            "modified_ts": modified_ts,
            "raw": data.hex(),
        }

    def _flush_pending_slot(self, index: int) -> None:
        """Merge buffered config/name (received before first index) into favorites."""
        if self._pending_slot:
            _LOGGER.debug("Flushing pending slot data to index %d: %s", index, self._pending_slot)
            self.favorites.setdefault(index, {}).update(self._pending_slot)
            self._pending_slot = {}

    def _parse_config_data(self, data: bytearray) -> None:
        """Parse configuration data (names, indices, etc) from CHAR_LIST."""
        if not data:
            return

        _LOGGER.debug("[Config] Hex: %s | ASCII: %s", data.hex(), data.decode("utf-8", errors="ignore"))

        try:
            header = data[0]
            
            # Header 0x30 is an Index block (ASCII "0", followed by index "1", "2"...)
            if header == 0x30 and len(data) >= 2:
                index_str = data[1:2].decode("utf-8", errors="ignore")
                if index_str.isdigit():
                    index = int(index_str)
                    _LOGGER.debug("Active index (0x30): %d", index)
                    self.active_favorite = index
                    self._last_index_seen = index
                    self._flush_pending_slot(index)

            # Header 0x45 ('E') is another Index block
            elif header == 0x45 and len(data) >= 3:
                index_str = data[1:3].decode("utf-8", errors="ignore")
                if index_str.isdigit():
                    index = int(index_str)
                    _LOGGER.debug("Active index (0x45): %d", index)
                    self.active_favorite = index
                    self._last_index_seen = index
                    self._flush_pending_slot(index)

            # Header 0x07 is an ASCII name block
            elif header == 0x07:
                # Some packets have a leading null or control byte before the text
                start_idx = 1
                while start_idx < len(data) and data[start_idx] < 32:
                    start_idx += 1

                name_bytes = data[start_idx:]
                if 0x00 in name_bytes:
                    name_bytes = name_bytes[:name_bytes.find(0x00)]

                name = name_bytes.decode("utf-8", errors="ignore").strip()
                if name:
                    if self._egb_slot is not None:
                        _LOGGER.debug("Schedule name '%s' (slot %d)", name, self._egb_slot)
                        self.schedules.setdefault(self._egb_slot, {})["name"] = name
                    elif self._last_index_seen is None:
                        _LOGGER.debug("Buffering name '%s' (index not yet known)", name)
                        self._pending_slot["name"] = name
                    else:
                        _LOGGER.debug("Captured name '%s' (index %d)", name, self._last_index_seen)
                        self.schedules.setdefault(self._last_index_seen, {})["name"] = name

            # Header 0x01 — 15-byte = favorite (PGB), 20-byte = schedule (EGB)
            elif header == 0x01 and len(data) >= 13:
                if len(data) >= 20:
                    parsed = self._decode_schedule_block(data)
                    idx = self._egb_slot or self._last_index_seen
                    _LOGGER.debug("Schedule config slot %s: %s", idx, parsed)
                    if idx is not None:
                        self.schedules.setdefault(idx, {}).update(parsed)
                else:
                    parsed = self._decode_config_block(data)
                    _LOGGER.debug("Favorite config slot %s: %s", self._pgb_slot, parsed)
                    idx = self._pgb_slot or self._last_index_seen
                    if idx is None:
                        self._pending_slot.update(parsed)
                    else:
                        self.favorites.setdefault(idx, {}).update(parsed)

            # Header 0x4F ('OK')
            elif data.startswith(b"OK"):
                _LOGGER.debug("Device acknowledged command (OK)")

        except Exception as e:
            _LOGGER.debug("Failed to parse config notification: %r", e)

    def _parse_data(self, data: bytearray) -> bool:
        """Parse raw device data and update state. Returns True if state changed."""
        _LOGGER.debug("Parsing raw data: %s", data.hex())
        try:
            # Find Color Section 'C' (0x43)
            c_idx = data.find(0x43)
            if c_idx != -1 and len(data) >= c_idx + 5:
                red, green, blue, brightness = data[c_idx + 1 : c_idx + 5]
                new_color = (red, green, blue)
                new_brightness = brightness
            else:
                new_color = self.color
                new_brightness = self.brightness

            # Find Sound Section 'S' (0x53)
            s_idx = data.find(0x53)
            if s_idx != -1 and len(data) >= s_idx + 3:
                sound_id = data[s_idx + 1]
                try:
                    new_sound = PyHatchBabyRestSound(sound_id)
                except ValueError:
                    _LOGGER.debug("Unknown sound ID received: %d", sound_id)
                    new_sound = self.sound # Keep current if unknown
                new_volume = data[s_idx + 2]
            else:
                new_sound = self.sound
                new_volume = self.volume

            # Find Power Section 'P' (0x50)
            p_idx = data.find(0x50)
            if p_idx != -1 and len(data) >= p_idx + 2:
                new_power = not bool(int("11000000", 2) & data[p_idx + 1])
            else:
                new_power = self.power

            if (
                new_color == self.color
                and new_brightness == self.brightness
                and new_sound == self.sound
                and new_volume == self.volume
                and new_power == self.power
            ):
                return False

            self.color = new_color
            self.brightness = new_brightness
            self.sound = new_sound
            self.volume = new_volume
            self.power = new_power

            _LOGGER.debug(
                "Parsed state (CHANGED): power=%s, brightness=%s, color=%s, sound=%s, volume=%s",
                self.power, self.brightness, self.color, self.sound, self.volume,
            )
            return True
        except (ValueError, IndexError) as e:
            _LOGGER.warning("Failed to parse device data: %r", e)
        return False

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

    async def select_favorite(self, index: int):
        """Select a favorite by index."""
        command = f"PSB{index:02X}"
        _LOGGER.debug("API command: select_favorite %d", index)
        return await self._send_command(command)

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
