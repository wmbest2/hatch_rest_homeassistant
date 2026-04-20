"""Tests for Hatch Rest API."""

from datetime import datetime
from time import monotonic
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from bleak.backends.device import BLEDevice
from bleak_retry_connector import BleakConnectionError

from custom_components.hatch_rest.api import PyHatchBabyRestAsync
from custom_components.hatch_rest.const import CHAR_TX, PyHatchBabyRestSound


def _assert_value(check_val: list[str], index: int, assert_val: str):
    if check_val[index] != assert_val:
        raise ValueError(f'response[{index}] "{check_val[index]}" != "{assert_val}"')


class TestAssertValue:
    """Tests for _assert_value helper."""

    def test_assert_value_passes(self):
        values = ["0x00", "0x43", "0x53"]
        _assert_value(values, 1, "0x43")

    def test_assert_value_fails(self):
        values = ["0x00", "0x43", "0x53"]
        with pytest.raises(ValueError, match='response\\[1\\] "0x43" != "0x99"'):
            _assert_value(values, 1, "0x99")


class TestPyHatchBabyRestAsync:
    """Tests for PyHatchBabyRestAsync."""

    @pytest.fixture
    def api(self, mock_ble_device: BLEDevice) -> PyHatchBabyRestAsync:
        """Create API instance."""
        api = PyHatchBabyRestAsync(mock_ble_device)
        yield api
        if api._disconnect_timer:
            api._disconnect_timer.cancel()
            api._disconnect_timer = None

    def test_init(self, api: PyHatchBabyRestAsync, mock_ble_device: BLEDevice):
        """Test API initialization."""
        assert api.device == mock_ble_device
        assert api.address == mock_ble_device.address
        assert api.color is None
        assert api.brightness is None
        assert api.sound is None
        assert api.volume is None
        assert api.power is None

    def test_name_property(self, api: PyHatchBabyRestAsync):
        """Test name property returns device name."""
        assert api.name == "Hatch Rest"

    @pytest.mark.asyncio
    async def test_client_connect_success(self, api: PyHatchBabyRestAsync):
        """Test successful client connection."""
        mock_client = MagicMock()
        mock_client.is_connected = True

        with patch(
            "custom_components.hatch_rest.api.establish_connection",
            new_callable=AsyncMock,
            return_value=mock_client,
        ):
            await api._client_connect()
            assert api._client == mock_client

    @pytest.mark.asyncio
    async def test_client_connect_failure(self, api: PyHatchBabyRestAsync):
        """Test client connection failure is handled."""
        with patch(
            "custom_components.hatch_rest.api.establish_connection",
            new_callable=AsyncMock,
            side_effect=BleakConnectionError("Connection failed"),
        ):
            await api._client_connect()
            assert api._client is None

    @pytest.mark.asyncio
    async def test_client_disconnect_when_idle(self, api: PyHatchBabyRestAsync):
        """Test client disconnects when no active operations."""
        mock_client = AsyncMock()
        mock_client.disconnect = AsyncMock()
        api._client = mock_client
        api._active_operations = 0

        await api._client_disconnect()
        mock_client.disconnect.assert_called_once()

    @pytest.mark.asyncio
    async def test_client_no_disconnect_when_busy(self, api: PyHatchBabyRestAsync):
        """Test client doesn't disconnect with active operations."""
        mock_client = AsyncMock()
        mock_client.disconnect = AsyncMock()
        api._client = mock_client
        api._active_operations = 1

        await api._client_disconnect()
        mock_client.disconnect.assert_not_called()

    @pytest.mark.asyncio
    async def test_refresh_data_parses_response(self, api: PyHatchBabyRestAsync):
        """Test refresh_data correctly parses device response."""
        # Simulated raw response from device
        # Format: [..., 0x43, R, G, B, brightness, 0x53, sound, volume, 0x50, power_byte]
        raw_response = bytearray(
            [
                0x00,
                0x00,
                0x00,
                0x00,
                0x00,  # padding (indices 0-4)
                0x43,  # color marker (index 5)
                0xFF,
                0x80,
                0x40,
                0x64,  # R, G, B, brightness (indices 6-9)
                0x53,  # audio marker (index 10)
                0x05,
                0x64,  # sound (ocean=5), volume (100) (indices 11-12)
                0x50,  # power marker (index 13)
                0x00,  # power byte (0x00 = ON, 0xC0 = OFF) (index 14)
            ]
        )

        mock_client = AsyncMock()
        mock_client.read_gatt_char = AsyncMock(return_value=raw_response)
        api._client = mock_client

        with patch.object(api, "_client_connect", new_callable=AsyncMock):
            with patch.object(api, "_client_disconnect", new_callable=AsyncMock):
                await api.refresh_data()

        assert api.color == (255, 128, 64)
        assert api.brightness == 100
        assert api.sound == PyHatchBabyRestSound.ocean
        assert api.volume == 100
        assert api.power is True
        assert api.active_favorite == 0

    @pytest.mark.asyncio
    async def test_turn_power_on(self, api: PyHatchBabyRestAsync):
        """Test turn_power_on sends correct command."""
        with patch.object(api, "_send_command", new_callable=AsyncMock) as mock_send:
            await api.turn_power_on()
            mock_send.assert_called_once_with("SI01", response=False)

    @pytest.mark.asyncio
    async def test_turn_power_off(self, api: PyHatchBabyRestAsync):
        """Test turn_power_off sends correct command."""
        with patch.object(api, "_send_command", new_callable=AsyncMock) as mock_send:
            await api.turn_power_off()
            mock_send.assert_called_once_with("SI00", response=False)

    @pytest.mark.asyncio
    async def test_set_sound(self, api: PyHatchBabyRestAsync):
        """Test set_sound sends correct command."""
        with patch.object(api, "_send_command", new_callable=AsyncMock) as mock_send:
            await api.set_sound(PyHatchBabyRestSound.rain)
            mock_send.assert_called_once_with("SN07", response=False)  # rain = 7

    @pytest.mark.asyncio
    async def test_set_volume(self, api: PyHatchBabyRestAsync):
        """Test set_volume sends correct command."""
        with patch.object(api, "_send_command", new_callable=AsyncMock) as mock_send:
            await api.set_volume(128)
            mock_send.assert_called_once_with("SV80", response=False)  # 128 in hex

    @pytest.mark.asyncio
    async def test_set_color(self, api: PyHatchBabyRestAsync):
        """Test set_color sends correct command."""
        api.brightness = 100
        with patch.object(api, "_send_command", new_callable=AsyncMock) as mock_send:
            await api.set_color(255, 128, 64)
            mock_send.assert_called_once_with("SCff804064", response=False)

    @pytest.mark.asyncio
    async def test_set_brightness(self, api: PyHatchBabyRestAsync):
        """Test set_brightness sends correct command."""
        api.color = (255, 128, 64)
        with patch.object(api, "_send_command", new_callable=AsyncMock) as mock_send:
            await api.set_brightness(200)
            mock_send.assert_called_once_with("SCff8040c8", response=False)  # 200 in hex = c8

    @pytest.mark.asyncio
    async def test_send_command_writes_to_characteristic(
        self, api: PyHatchBabyRestAsync
    ):
        """Test _send_command writes to correct characteristic."""
        mock_client = AsyncMock()
        mock_client.write_gatt_char = AsyncMock()
        api._client = mock_client

        with patch.object(api, "_client_connect", new_callable=AsyncMock):
            with patch.object(api, "refresh_data", new_callable=AsyncMock):
                with patch("asyncio.sleep", new_callable=AsyncMock):
                    await api._send_command("SI01")

        mock_client.write_gatt_char.assert_called_once_with(
            char_specifier=CHAR_TX,
            data=bytearray("SI01", "utf-8"),
            response=True,
        )

    @pytest.mark.asyncio
    async def test_select_favorite(self, api: PyHatchBabyRestAsync):
        """Test select_favorite sends correct command."""
        with patch.object(api, "_send_command", new_callable=AsyncMock) as mock_send:
            await api.select_favorite(2)
            mock_send.assert_called_once_with("SP02")

    @pytest.mark.asyncio
    async def test_toggle_favorite(self, api: PyHatchBabyRestAsync):
        """Test toggle_favorite sends correct commands."""
        with patch.object(api, "_send_commands", new_callable=AsyncMock) as mock_send:
            await api.toggle_favorite(2, True)
            mock_send.assert_called_once_with(["PSB02", "PSLC0", "PSF"])

    @pytest.mark.asyncio
    async def test_client_connect_syncs_clock(self, api: PyHatchBabyRestAsync):
        """Test that clock is synced upon connection."""
        mock_client = MagicMock()
        mock_client.is_connected = True
        mock_client.start_notify = AsyncMock()
        mock_client.write_gatt_char = AsyncMock()

        fixed_now = datetime(2026, 4, 20, 10, 0, 0)
        with patch("custom_components.hatch_rest.api.datetime") as mock_datetime:
            mock_datetime.now.return_value = fixed_now
            mock_datetime.strftime = datetime.strftime

            with patch(
                "custom_components.hatch_rest.api.establish_connection",
                new_callable=AsyncMock,
                return_value=mock_client,
            ):
                # Scenario 1: Full fetch (last_full_fetch is None)
                api._last_full_fetch = None
                await api._client_connect()
                mock_client.write_gatt_char.assert_any_call(
                    CHAR_TX, bytearray(b"ST20260420100000U"), response=False
                )
                
                # Scenario 2: Recent fetch (sync should still happen)
                mock_client.write_gatt_char.reset_mock()
                api._client = None # Reset to trigger connect
                api._last_full_fetch = monotonic()
                await api._client_connect()
                mock_client.write_gatt_char.assert_any_call(
                    CHAR_TX, bytearray(b"ST20260420100000U"), response=False
                )

    def test_active_operations_tracking(self, api: PyHatchBabyRestAsync):
        """Test active operations counter."""
        assert api._active_operations == 0
        api._set_active_operations(1)
        assert api._active_operations == 1
        api._set_active_operations(1)
        assert api._active_operations == 2
        api._set_active_operations(-1)
        assert api._active_operations == 1
