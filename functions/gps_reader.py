import serial
import pynmea2
import threading
import time


class GPSReader:
    """Reads GPS data from NEO-6M module via serial port."""

    def __init__(self, port='/dev/ttyUSB0', baudrate=9600):
        self.port = port
        self.baudrate = baudrate
        self._latitude = None
        self._longitude = None
        self._lock = threading.Lock()
        self._running = False
        self._thread = None
        self._serial = None

        try:
            self._serial = serial.Serial(port, baudrate, timeout=1)
            self._running = True
            self._thread = threading.Thread(target=self._read_loop, daemon=True)
            self._thread.start()
            print(f"[GPS] Connected on {port} at {baudrate} baud")
        except serial.SerialException as e:
            print(f"[GPS] Error opening {port}: {e}")
            raise

    def _read_loop(self):
        """Background thread that continuously reads NMEA sentences."""
        while self._running:
            try:
                if self._serial and self._serial.in_waiting:
                    line = self._serial.readline().decode('ascii', errors='replace').strip()
                    if line.startswith('$GPGGA') or line.startswith('$GPRMC'):
                        try:
                            msg = pynmea2.parse(line)
                            if hasattr(msg, 'latitude') and hasattr(msg, 'longitude'):
                                if msg.latitude != 0.0 and msg.longitude != 0.0:
                                    with self._lock:
                                        self._latitude = msg.latitude
                                        self._longitude = msg.longitude
                        except pynmea2.ParseError:
                            pass
                else:
                    time.sleep(0.1)
            except Exception as e:
                print(f"[GPS] Read error: {e}")
                time.sleep(1)

    def get_position(self):
        """Returns (latitude, longitude) or (None, None) if no fix."""
        with self._lock:
            return self._latitude, self._longitude

    def has_fix(self):
        """Returns True if GPS has a valid position fix."""
        with self._lock:
            return self._latitude is not None and self._longitude is not None

    def wait_for_fix(self, timeout=60):
        """Block until GPS gets a fix or timeout (seconds)."""
        print("[GPS] Waiting for satellite fix...")
        start = time.time()
        while time.time() - start < timeout:
            if self.has_fix():
                lat, lon = self.get_position()
                print(f"[GPS] Fix acquired: {lat}, {lon}")
                return True
            time.sleep(1)
        print("[GPS] Timeout waiting for fix")
        return False

    def close(self):
        """Stop reading and close serial port."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=2)
        if self._serial and self._serial.is_open:
            self._serial.close()
            print("[GPS] Serial port closed")
