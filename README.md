# Hatch Rest – Home Assistant Integration

This is a custom Home Assistant integration for controlling the **Hatch Rest** (1st-generation) nightlight and sound machine over Bluetooth Low Energy (BLE).

It provides a fully asynchronous, locally-controlled interface using a rewritten BLE API based on — and with gratitude to — the original work by **kjoconnor** in the `pyhatchbabyrest` project.

## ✨ Features

* **Local BLE control** — no cloud required
* **Efficient Connection Management** — Batches multiple commands into a single session and holds the connection open for 10 seconds of idle time to prevent "thrashing" the Bluetooth radio during heavy changes.
* **Smart Refresh** — Detects physical changes via BLE advertisements and schedules a debounced deep refresh (10s) to keep HA perfectly in sync.
* **Live Timer Tracking** — High-accuracy local timer estimation with a live-countdown `TIMESTAMP` sensor.
* **Enhanced Reliability** — Uses "write with response" for all GATT commands and optimized batch sequencing to prevent device corruption.
* **Configurable Polling** — Adjust the update frequency (default: 10 min) via the integration options flow.
* **Dynamic Favorites** — Automatically filters the favorites list to only show those enabled in your Hatch settings.
* **Full Protocol Support** — Comprehensive implementation of the Hatch Rest BLE protocol, including favorites (PGB), schedules (EGB), and timers (SD/GI/GD).

## 📦 Installation

### HACS

1. Add this repository as a **Custom Repository**
   *(HACS → Integrations → Custom Repositories)*
2. Search for **Hatch Rest**
3. Install → Restart Home Assistant

## 🔍 Adding the Device

1. Go to **Settings → Devices & Services → Add Integration**
2. Search for **Hatch Rest**
3. Choose your discovered device from the list
4. Configure the **Scan Interval** (default is 10 minutes)
5. Done!

## 🧩 Supported Entities

### 🔌 Switch
* **Master Switch**: Main power state of the device.
* **Favorite Enabled**: Configuration toggles to enable or disable specific favorite slots.
* **Schedule Enabled**: Configuration toggles to enable or disable specific schedule slots.

### 🟡 Light
* RGB color and brightness control.

### 🔊 Media Player
* Sound selection and volume control.

### 📋 Select
* **Favorite**: Quickly switch between your pre-configured favorites. Only enabled favorites are shown.

### ⏲️ Sensor
* **Timer Remaining**: A live-updating timestamp sensor showing when the active timer will expire.

### 🔢 Number
* **Set Timer**: A numeric input to quickly set a timer (in minutes) from the dashboard.

## 📡 Bluetooth Requirements

Because the Hatch Rest is a BLE device:

* A compatible Home Assistant Bluetooth controller is required.
* This integration uses `bleak_retry_connector` for robust connection management.

## 🧪 Contributing

Issues and PRs are welcome!
