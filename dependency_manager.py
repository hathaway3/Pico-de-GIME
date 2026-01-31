import network
import time
import machine

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

    def ensure_package(self, package_name, import_name=None):
        """
        Checks if a package is installed. If not, attempts to install it via mip.
        :param package_name: The name of the package to install via mip (e.g. 'microdot')
        :param import_name: The name used to import the package (defaults to package_name)
        """
        if import_name is None:
            import_name = package_name

        try:
            # Try to import the package
            __import__(import_name)
            # print(f"BM: Package '{import_name}' is present.")
            return True
        except ImportError:
            print(f"BM: Package '{import_name}' not found. Attempting to install...")
            
            if self.connect_wifi():
                try:
                    import mip
                    mip.install(package_name)
                    print(f"BM: Package '{package_name}' installed successfully.")
                    return True
                except Exception as e:
                    print(f"BM: Failed to install '{package_name}': {e}")
                    return False
            else:
                print("BM: Cannot install package without WiFi.")
                return False
