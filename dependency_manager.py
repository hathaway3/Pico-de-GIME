import network
import time
import machine
import urequests
import os

class DependencyManager:
    def __init__(self, ssid, password):
        self.ssid = ssid
        self.password = password

    def connect_wifi(self):
        """Connects to WiFi if not already connected."""
        wlan = network.WLAN(network.STA_IF)
        wlan.active(True)
        if not wlan.isconnected():
            print(f"BM: Connecting to WiFi {self.ssid} to check dependencies...")
            wlan.connect(self.ssid, self.password)
            wait_count = 0
            while not wlan.isconnected() and wait_count < 20:
                time.sleep(0.5)
                wait_count += 1
            
            if wlan.isconnected():
                print("BM: WiFi Connected!")
                return True
            else:
                print("BM: WiFi Connection Failed.")
                return False
        return True

    def install_from_urls(self, package_dir, file_map):
        """
        Manually installs files from URLs.
        :param package_dir: Local directory (e.g. 'lib/microdot')
        :param file_map: Dict of 'filename' -> 'CMD_URL'
        """
        if not self.connect_wifi():
            return False

        try:
            # Create directory if needed
            try:
                os.mkdir('lib')
            except OSError: pass
            
            # Recursive directory creation is hard in basic MicroPython, assume simple structure
            # Handle nested package_dir like 'lib/microdot'
            parts = package_dir.split('/')
            path = ""
            for part in parts:
                path += part
                try:
                    os.mkdir(path)
                except OSError: pass
                path += "/"

            for filename, url in file_map.items():
                target_path = f"{package_dir}/{filename}"
                print(f"BM: Downloading {filename}...")
                
                response = urequests.get(url)
                if response.status_code == 200:
                    with open(target_path, 'w') as f:
                        f.write(response.text)
                    print(f"BM: Saved {target_path}")
                else:
                    print(f"BM: Failed to download {url} (Status {response.status_code})")
                    return False
                response.close()
            
            return True
        except Exception as e:
            print(f"BM: Manual install error: {e}")
            return False

    def ensure_package(self, package_name, import_name=None, manual_files=None):
        """
        Checks if a package is installed. If not, attempts to install via mip or manual URLs.
        :param manual_files: Dict of filename->URL for manual installation fallback
        """
        if import_name is None:
            if "github:" in package_name or "/" in package_name:
                 # If user didn't supply import_name but provided a URL, we can't safely proceed without manual_files
                 pass 
            import_name = package_name

        try:
            # Try to import
            __import__(import_name)
            return True
        except ImportError:
            print(f"BM: Package '{import_name}' not found. Attempting to install...")
            
            if not self.connect_wifi():
                print("BM: WiFi required.")
                return False

            # Strategy 1: MIP
            if not manual_files:
                try:
                    import mip
                    mip.install(package_name)
                    # Verify
                    try:
                        __import__(import_name)
                        print(f"BM: Verified '{import_name}'.")
                        return True
                    except ImportError:
                        pass # proceed to failure
                except Exception as e:
                    print(f"BM: MIP install failed: {e}")
            
            # Strategy 2: Manual Fallback
            if manual_files:
                print("BM: Attempting manual installation from source...")
                # Assume package_name is the target dir name in lib/ if manually installing
                # Actually, let's assume 'lib/' + import_name normally
                target_dir = f"lib/{import_name}"
                if self.install_from_urls(target_dir, manual_files):
                    return True
            
            return False
