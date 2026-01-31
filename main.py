import machine
import network
import uasyncio as asyncio
import json
import gc
import sys
import time
import os
import dependency_manager

# Add lib to path if not present
if '/lib' not in sys.path:
    sys.path.append('/lib')

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
    "WEB_PORT": 80
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

# --- LOGGING ---
class Logger:
    def info(self, msg):
        print(f"[INFO] {time.ticks_ms()/1000:.3f}: {msg}")

    def warn(self, msg):
        print(f"[WARN] {time.ticks_ms()/1000:.3f}: {msg}")

    def error(self, msg):
        print(f"[ERROR] {time.ticks_ms()/1000:.3f}: {msg}")

log = Logger()

# --- HARDWARE INITIALIZATION ---
try:
    uart = machine.UART(CONFIG["UART_ID"], baudrate=CONFIG["BAUD"], tx=machine.Pin(0), rx=machine.Pin(1), timeout=0)
    wdt = machine.WDT(timeout=8000) # Watchdog timer (8 seconds)
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

@app.route('/ws')
@with_websocket
async def coco_socket(request, ws):
    log.info("Client connected")
    await ws.send(json.dumps({"status": "BOOT_READY"}))
    protocol = WindIntProtocol()
    
    # Task: UART -> Browser
    async def uart_to_browser():
        try:
            while True:
                wdt.feed()
                if uart.any():
                    chunk = uart.read()
                    if chunk:
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
                uart.write(data)
    except Exception as e:
        log.error(f"WS Error: {e}")
    finally:
        sender_task.cancel()
        log.info("Client disconnected")

async def wifi_manager():
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    while True:
        try:
            if not wlan.isconnected():
                log.info("Connecting to Wi-Fi...")
                wlan.connect(CONFIG["SSID"], CONFIG["PASS"])
                
                # Wait for connection
                for _ in range(20):
                    if wlan.isconnected(): break
                    await asyncio.sleep(0.5)
                
                if wlan.isconnected():
                    log.info(f"WiFi Connected: {wlan.ifconfig()[0]}")
                else:
                    log.warn("WiFi Connection Failed. Retrying...")
            
            # Periodic GC
            gc.collect()
            await asyncio.sleep(30)
        except Exception as e:
            log.error(f"WiFi Manager Error: {e}")
            await asyncio.sleep(5)

async def run_app():
    asyncio.create_task(wifi_manager())
    log.info(f"Starting Web Server on port {CONFIG['WEB_PORT']}...")
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