import pytest
from unittest.mock import MagicMock
from bleak.backends.device import BLEDevice

@pytest.fixture
def mock_ble_device() -> BLEDevice:
    """Create a mock BLE device."""
    device = MagicMock(spec=BLEDevice)
    device.address = "AA:BB:CC:DD:EE:FF"
    device.name = "Hatch Rest"
    return device
