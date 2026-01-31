# Pico-de-GIME 🌶️

**Wireless WindInt Terminal Bridge for CoCo 3**

Pico-de-GIME is a MicroPython project for the Raspberry Pi Pico W that acts as a wireless bridge for the Tandy Color Computer 3 (CoCo 3). It receives "WindInt" protocol commands via UART (serial) and renders them in a modern web browser terminal, allowing you to use your CoCo 3 wirelessly with high-fidelity 80-column text and graphics.

## Features
- **WiFi-enabled**: Connects to your local network.
- **Web Terminal**: Access the CoCo 3 console from any browser on `http://<pico-ip>`.
- **WindInt Protocol**: Supports text positioning, colors, and basic vector graphics (Move, Line, Circle).
- **Bidirectional**: Type in the browser to send keystrokes back to the CoCo.
- **Auto-Provisioning**: Automatically installs dependencies (`microdot`) if WiFi is available.
- **Configurable**: Settings stored in `config.json`.

## Prerequisites
- **Raspberry Pi Pico W** running MicroPython firmware (latest version recommended).
- **Wiring**:
    - **Pico TX (Pin 0)** -> CoCo RX
    - **Pico RX (Pin 1)** -> CoCo TX
    - **GND** -> CoCo GND
    - Level shifter required if connecting directly to RS-232 levels!

## Installation

### 1. Prepare Dependencies
You have two options to install the required `microdot` library:

#### Option A: Auto-Install (Recommended)
Just configure your WiFi in `config.json` (see below). On first boot, the Pico will connect to the internet and download `microdot` automatically.

#### Option B: Local Build (Offline)
If you prefer to load files manually or don't have internet access for the Pico:
1.  Run `make` in this directory (requires `curl`).
2.  Copy the generated `lib/` folder to your Pico.

### 2. Configuration
Create a file named `config.json` on the Pico with your settings:

```json
{
    "SSID": "MyWifiNetwork",
    "PASS": "MyWifiPassword",
    "UART_ID": 0,
    "BAUD": 9600,
    "X_OFFSET": 32,
    "Y_OFFSET": 32,
    "WEB_PORT": 80
}
```
*Note: `X_OFFSET` and `Y_OFFSET` adjust coordinate mapping for the specific terminal emulation.*

### 3. Copy Files
Upload the following files to the root of your Pico W:
- `main.py`
- `dependency_manager.py`
- `index.html`
- `config.json`
- `lib/` (if using Option B)

## Usage
1.  **Power on** the Pico W.
2.  **Connect** to its serial console (USB) to see the IP address.
    - Example Log: `[INFO] 15.678: WiFi Connected: 192.168.1.105`
3.  **Open Browser**: Navigate to `http://192.168.1.105`.
4.  **Connect**: The terminal should show a synchronization message.
5.  **Enjoy**: Output from your CoCo 3 will appear in the browser window.

## Troubleshooting
- **No Connection**: Check `config.json` credentials. Watch the USB serial output for `[ERROR]` logs.
- **Garbage Text**: Verify `BAUD` rate matches your CoCo's serial settings.
- **Missing Libraries**: Ensure `lib/` folder exists or valid WiFi credentials are provided for auto-install.
