import sys
# Add lib to path if not present (needed before other imports that live in /lib)
if '/lib' not in sys.path:
    sys.path.append('/lib')

import machine
import network
import uasyncio as asyncio
import json
import gc
import time
import os
import dependency_manager

# Debug: Print Path
print(f"BM: sys.path: {sys.path}")


# --- PARAMETERS ---
DEFAULT_CONFIG = {
    "SSID": "Your_WiFi_Name",
    "PASS": "Your_WiFi_Password",
    "UART_ID": 0,
    "BAUD": 9600,
    "X_OFFSET": 32,
    "Y_OFFSET": 32,
    "WEB_PORT": 80,
    "WDT_ENABLED": True
}

def load_config():
    try:
        with open('config.json', 'r') as f:
            print("Loading config from config.json...")
            return json.load(f)
    except (OSError, ValueError):
        print("Warning: config.json missing or invalid. Using defaults.")
        return DEFAULT_CONFIG

CONFIG = load_config()

# --- DEPENDENCY CHECK ---
dm = dependency_manager.DependencyManager(CONFIG["SSID"], CONFIG["PASS"])

# Manual fallback URLs for Microdot in case mip fails
MICRODOT_FILES = {
    "__init__.py": "https://raw.githubusercontent.com/miguelgrinberg/microdot/main/src/microdot/__init__.py",
    "microdot.py": "https://raw.githubusercontent.com/miguelgrinberg/microdot/main/src/microdot/microdot.py",
    "helpers.py": "https://raw.githubusercontent.com/miguelgrinberg/microdot/main/src/microdot/helpers.py",
    "websocket.py": "https://raw.githubusercontent.com/miguelgrinberg/microdot/main/src/microdot/websocket.py",
    "cors.py": "https://raw.githubusercontent.com/miguelgrinberg/microdot/main/src/microdot/cors.py"
}

# Ensure microdot is installed (requires WiFi if missing)
# We try to use the manual file map if mip fails
if not dm.ensure_package("github:miguelgrinberg/microdot", "microdot", manual_files=MICRODOT_FILES):
    print("CRITICAL: Failed to load 'microdot'. System cannot start.")
    print("Please check your WiFi credentials in config.json or manually install 'microdot' in /lib.")
    # Blink LED or other error signal could go here
    pass

# Check if all files are valid Python
for fname in ['__init__.py', 'microdot.py', 'helpers.py', 'websocket.py', 'cors.py']:
    try:
        path = f'/lib/microdot/{fname}'
        with open(path, 'r') as f:
            content = f.read(50)
            if content.strip().startswith('<') or '404' in content: # Detect HTML or 404
                print(f"CRITICAL: {fname} appears to be invalid/HTML. Deleting...")
                # Cleanup
                try: 
                    for fn in os.listdir('/lib/microdot'):
                        os.remove(f'/lib/microdot/{fn}')
                    os.rmdir('/lib/microdot')
                except: pass
                break
            
            # Debug: print start of file
            print(f"BM: Checked {fname} OK. Start: {content[:20]}")
            
    except OSError:
        print(f"CRITICAL: {fname} is missing. Corrupt installation detected.")
        # Cleanup
        try: 
            for fn in os.listdir('/lib/microdot'):
                os.remove(f'/lib/microdot/{fn}')
            os.rmdir('/lib/microdot')
            print("Deleted corrupt microdot library. System will reboot to re-download.")
            time.sleep(2)
            machine.reset()
        except: 
            print("Cleanup failed. Please delete /lib/microdot manually.")
            pass
        break

try:
    from microdot import Microdot, send_file
    from microdot.websocket import with_websocket
except ImportError as e:
    print(f"Error: Microdot library import failed: {e}")
    print("Halted. (Boot loop prevented)")
    # Fallback or exit
    sys.exit(1)

# --- GLOBAL STATS ---
TX_BYTES = 0
RX_BYTES = 0
START_TIME = time.time()

# --- LOGGING ---
class Logger:
    def __init__(self):
        self.buffer = []
        self.max_lines = 20

    def _log(self, level, msg):
        timestamp = time.ticks_ms()/1000
        formatted = f"[{level}] {timestamp:.3f}: {msg}"
        print(formatted)
        
        # Add to buffer
        self.buffer.append(formatted)
        if len(self.buffer) > self.max_lines:
            self.buffer.pop(0)

    def info(self, msg): self._log("INFO", msg)
    def warn(self, msg): self._log("WARN", msg)
    def error(self, msg): self._log("ERROR", msg)
    
    def get_logs(self):
        return self.buffer

log = Logger()

# --- HARDWARE INITIALIZATION ---
try:
    # Map UART ID to (TX_PIN, RX_PIN)
    # UART0 usually on GP0/1, UART1 on GP4/5 for standard Pico pinout
    UART_PINS = {
        0: (0, 1),
        1: (4, 5)
    }
    
    uid = CONFIG.get("UART_ID", 0)
    tx_pin, rx_pin = UART_PINS.get(uid, (0, 1))
    
    log.info(f"Initializing UART {uid} on TX=GP{tx_pin}, RX=GP{rx_pin}")
    uart = machine.UART(uid, baudrate=CONFIG["BAUD"], tx=machine.Pin(tx_pin), rx=machine.Pin(rx_pin), timeout=0)
    
    if CONFIG.get("WDT_ENABLED", True):
        wdt = machine.WDT(timeout=8000) # Watchdog timer (8 seconds)
    else:
        wdt = None
        log.warn("Watchdog Timer DISABLED by config.")
except Exception as e:
    log.error(f"Hardware Params Init Failed: {e}")
    # Fatal error, but maybe we can still run without UART? typically no.
    raise

# CoCo 3 Palette (GIME standard to RGB)
PALETTE_RGB = [
    "#000000", "#0000AA", "#00AA00", "#00AAAA", 
    "#AA0000", "#AA00AA", "#AA5500", "#AAAAAA",
    "#555555", "#5555FF", "#55FF55", "#55FFFF", 
    "#FF5555", "#FF55FF", "#FFFF55", "#FFFFFF"
]

class WindIntProtocol:
    """State machine to parse OS-9 WindInt sequences."""
    NORMAL = 0
    GET_X = 1
    GET_Y = 2
    ESC_SEQ = 3
    GET_PARAMS = 4

    def __init__(self):
        self.state = self.NORMAL
        self.pending_cmd = None
        self.param_buffer = []
        self.param_count = 0

    async def process_byte(self, b, ws):
        try:
            if self.state == self.NORMAL:
                if b == 0x02: # Position Cursor
                    self.state = self.GET_X
                elif b == 0x01: await ws.send(json.dumps({"t": "txt", "d": "\x1b[H"}))
                elif b == 0x0C: await ws.send(json.dumps({"t": "txt", "d": "\x1b[2J\x1b[H", "clr_gfx": True}))
                elif b == 0x1B: self.state = self.ESC_SEQ
                else: await ws.send(json.dumps({"t": "txt", "d": chr(b)}))

            elif self.state == self.GET_X:
                self.x = b - CONFIG["X_OFFSET"]
                self.state = self.GET_Y

            elif self.state == self.GET_Y:
                y = b - CONFIG["Y_OFFSET"]
                await ws.send(json.dumps({"t": "txt", "d": f"\x1b[{y+1};{self.x+1}H"}))
                self.state = self.NORMAL

            elif self.state == self.ESC_SEQ:
                # Map GIME Graphics Commands
                if b == 0x41: # Move Pen
                    self.setup_params("move", 2)
                elif b == 0x42: # Draw Line
                    self.setup_params("line", 2)
                elif b == 0x43: # Circle
                    self.setup_params("circle", 1)
                elif b == 0x31: # Forecolor
                    self.setup_params("fcolor", 1)
                else:
                    self.state = self.NORMAL # Unsupported

            elif self.state == self.GET_PARAMS:
                self.param_buffer.append(b - CONFIG["X_OFFSET"])
                if len(self.param_buffer) == self.param_count:
                    await self.dispatch_gfx(ws)

        except Exception as e:
            log.error(f"Protocol Error: {e}")
            self.state = self.NORMAL

    def setup_params(self, cmd, count):
        self.pending_cmd = cmd
        self.param_count = count
        self.param_buffer = []
        self.state = self.GET_PARAMS

    async def dispatch_gfx(self, ws):
        msg = {"t": "gfx", "cmd": self.pending_cmd, "p": self.param_buffer}
        await ws.send(json.dumps(msg))
        self.state = self.NORMAL

# --- WEB SERVER ---
app = Microdot()

@app.route('/')
async def index(request):
    return send_file('index.html')

@app.route('/setup')
async def page_setup(request):
    return send_file('setup.html')

@app.route('/debug')
async def page_debug(request):
    return send_file('debug.html')

# --- API ---
@app.route('/api/autobaud')
async def api_autobaud(request):
    global uart
    rates = [300, 1200, 2400, 4800, 9600, 19200, 38400, 57600, 115200]
    scores = {}
    
    # Get current pins based on config
    uid = CONFIG.get("UART_ID", 0)
    # Using the same mapping logic as hardware init
    # Note: Ideally this mapping should be a global helper, but duplicating for safety/simplicity here
    uart_pins = {0: (0, 1), 1: (4, 5)} 
    tx_p, rx_p = uart_pins.get(uid, (0, 1))
    
    log.info("Starting Auto-Baud Scan...")
    
    best_rate = CONFIG["BAUD"]
    max_score = -1000
    
    original_baud = CONFIG["BAUD"]

    try:
        for rate in rates:
            # Re-init UART at new rate
            # We use a short timeout for the scan
            uart = machine.UART(uid, baudrate=rate, tx=machine.Pin(tx_p), rx=machine.Pin(rx_p), timeout=200)
            
            # Flush input
            while uart.any(): uart.read()
            
            # Listen for a short window
            await asyncio.sleep(0.3) 
            
            score = 0
            if uart.any():
                data = uart.read(64) # Read up to 64 bytes
                if data:
                    for b in data:
                        # Scoring Heuristic
                        if 32 <= b <= 126: score += 1 # Printable ASCII
                        elif b in (10, 13): score += 2 # Newlines are very good
                        elif b == 0: score -= 5 # Nulls are usually bad framing
                        elif b > 127: score -= 2 # High bits often mean wrong baud
                        else: score -= 1 # Other control chars
            
            log.info(f"Rate: {rate}, Score: {score}")
            scores[rate] = score
            
            if score > max_score and score > 0: # Threshold of 0 to ensure we actually saw *some* good data
                max_score = score
                best_rate = rate

    except Exception as e:
        log.error(f"Autobaud Error: {e}")
        
    # Restore original (or user will save the new one via UI)
    log.info(f"Auto-Baud Cycle Complete. Winner: {best_rate}")
    uart = machine.UART(uid, baudrate=original_baud, tx=machine.Pin(tx_p), rx=machine.Pin(rx_p), timeout=0)
    
    return json.dumps({"detected": best_rate, "scores": scores}), 200, {'Content-Type': 'application/json'}

@app.route('/api/status')
async def api_status(request):
    gc.collect() # Force cleanup to get a stable base reading
    wlan = network.WLAN(network.STA_IF)
    status = {
        "uptime": time.time() - START_TIME,
        "rssi": wlan.status('rssi') if wlan.isconnected() else 0,
        "free_ram": gc.mem_free(),
        "tx_bytes": TX_BYTES,
        "rx_bytes": RX_BYTES,
        "logs": log.get_logs()
    }
    return json.dumps(status), 200, {'Content-Type': 'application/json'}

@app.route('/api/config', methods=['GET', 'POST'])
async def api_config(request):
    if request.method == 'POST':
        try:
            new_config = request.json
            # Basic validation
            if "BAUD" in new_config: int(new_config["BAUD"])
            if "UART_ID" in new_config: int(new_config["UART_ID"])
            
            # Save
            with open('config.json', 'w') as f:
                json.dump(new_config, f)
            
            log.warn("Config updated via API. Rebooting...")
            asyncio.create_task(do_reboot())
            return json.dumps({"status": "OK", "msg": "Saved. Rebooting..."})
        except Exception as e:
            return json.dumps({"status": "ERROR", "msg": str(e)}), 400
            
    return json.dumps(CONFIG), 200, {'Content-Type': 'application/json'}

async def do_reboot():
    await asyncio.sleep(1)
    machine.reset()

@app.route('/ws')
@with_websocket
async def coco_socket(request, ws):
    global RX_BYTES, TX_BYTES
    log.info("Client connected")
    await ws.send(json.dumps({"status": "BOOT_READY"}))
    protocol = WindIntProtocol()
    
    # Task: UART -> Browser
    async def uart_to_browser():
        global RX_BYTES
        try:
            while True:
                # wdt.feed() handled by global heartbeat
                if uart.any():
                    chunk = uart.read()
                    if chunk:
                        RX_BYTES += len(chunk)
                        for byte in chunk:
                            await protocol.process_byte(byte, ws)
                await asyncio.sleep(0.01)
        except Exception as e:
            log.error(f"UART Reader Task Failed: {e}")
            raise

    # Start the background task
    sender_task = asyncio.create_task(uart_to_browser())

    try:
        while True:
            data = await ws.receive()
            if data:
                TX_BYTES += len(data)
                uart.write(data)
    except Exception as e:
        log.error(f"WS Error: {e}")
    finally:
        sender_task.cancel()
        log.info("Client disconnected")

async def wifi_manager():
    wlan_sta = network.WLAN(network.STA_IF)
    wlan_sta.active(True)
    
    # Try to connect
    log.info(f"Connecting to WiFi: {CONFIG['SSID']}...")
    try:
        wlan_sta.connect(CONFIG["SSID"], CONFIG["PASS"])
    except OSError as e:
        log.error(f"WiFi Connection Error: {e}")

    # Wait for connection (30 seconds timeout)
    for i in range(30):
        if wlan_sta.isconnected():
            break
        await asyncio.sleep(1)
        
    if wlan_sta.isconnected():
        log.info(f"WiFi Connected: {wlan_sta.ifconfig()[0]}")
    else:
        log.warn("WiFi Connection Failed. Enabling RESCUE MODE (AP).")
        wlan_sta.active(False) # Disable STA to save power/conflicts
        
        # Enable Access Point
        wlan_ap = network.WLAN(network.AP_IF)
        wlan_ap.active(True)
        wlan_ap.config(essid='Pico-GIME-Rescue', security=0) # Explicitly OPEN
        
        log.warn("!! RESCUE MODE ACTIVE !!")
        log.warn("Connect to WiFi: 'Pico-GIME-Rescue'")
        log.warn(f"Go to: http://{wlan_ap.ifconfig()[0]} to fix config.")

    # Periodic GC loop
    while True:
        gc.collect()
        await asyncio.sleep(30)

async def heartbeat():
    """Feeds the watchdog timer to keep the system alive."""
    if wdt is None:
        log.info("Heartbeat task skipped (WDT disabled).")
        return
        
    log.info("Heartbeat task started.")
    while True:
        wdt.feed()
        await asyncio.sleep(1)

async def run_app():
    asyncio.create_task(heartbeat())
    asyncio.create_task(wifi_manager())
    log.info(f"Starting Web Server (Pico-GIME v1.2 Auto-Baud) on port {CONFIG['WEB_PORT']}...")
    await app.start_server(port=CONFIG["WEB_PORT"])

if __name__ == '__main__':
    try:
        # Start the event loop
        asyncio.run(run_app())
    except KeyboardInterrupt:
        log.info("System stopped by user.")
    except Exception as e:
        log.error(f"Critical System Failure: {e}")
        time.sleep(1)
        machine.reset()
