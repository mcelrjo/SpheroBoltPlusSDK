from enum import Enum
import asyncio
import struct
import logging
from typing import List, Optional, Tuple, Dict, Any, Union

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("SpheroSDK")

# Constants for BLE communication
SPHERO_SERVICE_UUID = "00010001-574F-4F20-5370-6865726F2121"
SPHERO_CHARACTERISTIC_UUID = "00010002-574F-4F20-5370-6865726F2121"
BOLT_NAME_PREFIX = "SB-"

# SOP (Start of Packet) and EOP (End of Packet) values
SOP = 0x8D
EOP = 0xD8


class PowerModes(Enum):
    SLEEP = 0
    AWAKE = 1


class SpheroBoltPlus:
    """Main class for interacting with a Sphero Bolt Plus robot"""

    def __init__(self):
        self.device = None
        self.characteristic = None
        self.is_connected = False
        self.is_awake = False

        # Import BLE library conditionally to support different platforms
        try:
            import bleak
            self.ble = bleak
            self.client = bleak.BleakClient
            self.scanner = bleak.BleakScanner
        except ImportError:
            logger.error("bleak library not found. Please install with: pip install bleak")
            raise ImportError("Required package 'bleak' not installed")

    async def scan_for_devices(self, timeout: int = 5) -> List[Dict[str, Any]]:
        """
        Scan for nearby Sphero BOLT devices

        Args:
            timeout: Scan duration in seconds

        Returns:
            List of dictionaries containing device info
        """
        logger.info(f"Scanning for Sphero BOLT devices for {timeout} seconds...")

        devices = await self.scanner.discover(timeout=timeout)
        sphero_devices = []

        for device in devices:
            if device.name and device.name.startswith(BOLT_NAME_PREFIX):
                # Filter to ensure device is advertising the Sphero service
                if not device.metadata.get("uuids") or SPHERO_SERVICE_UUID not in device.metadata.get("uuids", []):
                    continue

                # Add device with calculated distance (approximated from RSSI)
                sphero_devices.append({
                    'address': device.address,
                    'name': device.name,
                    'rssi': device.rssi,
                    'approximate_distance': self._calculate_distance_from_rssi(device.rssi)
                })

        return sorted(sphero_devices, key=lambda x: x['rssi'], reverse=True)

    def _calculate_distance_from_rssi(self, rssi: int) -> float:
        """
        Calculate approximate distance from RSSI value
        This is a rough approximation

        Args:
            rssi: RSSI value in dBm

        Returns:
            Approximate distance in meters
        """
        # Simple approximation formula
        # Actual distance calculation would require environment calibration
        if rssi == 0:
            return float('inf')
        else:
            # Approximation based on free-space path loss
            # Measured power (txPower) is the RSSI at 1 meter distance
            # For Sphero, this is approximately -59 dBm (may need adjustment)
            tx_power = -59
            ratio = rssi / tx_power
            if ratio < 1:
                return ratio ** 10
            else:
                return 0.89976 * (ratio ** 7.7095) + 0.111

    async def connect(self, address: str) -> bool:
        """
        Connect to a Sphero device by address

        Args:
            address: MAC address or UUID of the device

        Returns:
            True if connection successful, False otherwise
        """
        logger.info(f"Connecting to Sphero BOLT at address: {address}")

        try:
            self.device = self.client(address)
            await self.device.connect()

            # Get the API characteristic
            self.characteristic = self.device.services.get_characteristic(SPHERO_CHARACTERISTIC_UUID)
            if not self.characteristic:
                logger.error("Could not find API characteristic")
                await self.device.disconnect()
                return False

            self.is_connected = True
            logger.info("Successfully connected to Sphero BOLT")

            # Wake up the robot
            await self.wake()

            return True

        except Exception as e:
            logger.error(f"Connection failed: {str(e)}")
            self.is_connected = False
            return False

    async def disconnect(self) -> bool:
        """
        Disconnect from the Sphero device

        Returns:
            True if disconnection successful, False otherwise
        """
        if not self.is_connected:
            return True

        try:
            await self.device.disconnect()
            self.is_connected = False
            self.is_awake = False
            logger.info("Disconnected from Sphero BOLT")
            return True
        except Exception as e:
            logger.error(f"Disconnection failed: {str(e)}")
            return False

    async def wake(self) -> bool:
        """
        Wake up the robot

        Returns:
            True if wake command sent successfully, False otherwise
        """
        # Wake command bytes as specified in the documentation
        wake_command = bytearray([0x8D, 0x38, 0x11, 0x01, 0x13, 0x0D, 0xFF, 0x96, 0xD8])

        try:
            await self.device.write_gatt_char(SPHERO_CHARACTERISTIC_UUID, wake_command)
            self.is_awake = True
            logger.info("Wake command sent to Sphero BOLT")
            return True
        except Exception as e:
            logger.error(f"Failed to send wake command: {str(e)}")
            return False

    async def drive(self, speed: int, heading: int, reverse: bool = False) -> bool:
        """
        Drive the Sphero at specified speed and heading

        Args:
            speed: Speed value from 0-255
            heading: Heading angle from 0-359 degrees
            reverse: If True, drive in reverse

        Returns:
            True if command sent successfully, False otherwise
        """
        if not self.is_connected or not self.is_awake:
            logger.error("Device not connected or not awake")
            return False

        # Validate parameters
        speed = max(0, min(255, speed))
        heading = max(0, min(359, heading))
        flags = 1 if reverse else 0

        # Construct the payload
        payload = struct.pack(">BHB", speed, heading, flags)

        # Header for drive command
        header = bytearray([0x38, 0x12, 0x01, 0x16, 0x07, 0xFF])

        # Send the command
        return await self._send_command(header, payload)

    async def set_main_led(self, red: int, green: int, blue: int) -> bool:
        """
        Set the main LED color

        Args:
            red: Red value (0-255)
            green: Green value (0-255)
            blue: Blue value (0-255)

        Returns:
            True if command sent successfully, False otherwise
        """
        if not self.is_connected or not self.is_awake:
            logger.error("Device not connected or not awake")
            return False

        # Validate parameters
        red = max(0, min(255, red))
        green = max(0, min(255, green))
        blue = max(0, min(255, blue))

        # Construct the payload
        payload = struct.pack(">BBB", red, green, blue)

        # Header for set RGB LED command
        header = bytearray([0x38, 0x11, 0x01, 0x20, 0x07, 0xFF])

        # Send the command
        return await self._send_command(header, payload)

    async def set_matrix_led(self, x: int, y: int, red: int, green: int, blue: int) -> bool:
        """
        Set specific LED in matrix to specified color

        Args:
            x: X position in matrix (0-7)
            y: Y position in matrix (0-7)
            red: Red value (0-255)
            green: Green value (0-255)
            blue: Blue value (0-255)

        Returns:
            True if command sent successfully, False otherwise
        """
        if not self.is_connected or not self.is_awake:
            logger.error("Device not connected or not awake")
            return False

        # Validate parameters
        x = max(0, min(7, x))
        y = max(0, min(7, y))
        red = max(0, min(255, red))
        green = max(0, min(255, green))
        blue = max(0, min(255, blue))

        # Construct the payload
        payload = struct.pack(">BBBBB", x, y, red, green, blue)

        # Header for set matrix LED command
        header = bytearray([0x38, 0x11, 0x01, 0x2D, 0x09, 0xFF])

        # Send the command
        return await self._send_command(header, payload)

    async def _send_command(self, header: bytearray, payload: bytes) -> bool:
        """
        Send a command to the Sphero

        Args:
            header: Command header bytes
            payload: Command payload bytes

        Returns:
            True if command sent successfully, False otherwise
        """
        if not self.is_connected:
            logger.error("Device not connected")
            return False

        # Calculate checksum
        checksum = self._calculate_checksum(header + payload)

        # Construct full packet
        packet = bytearray([SOP]) + header + payload + bytearray([checksum, EOP])

        try:
            await self.device.write_gatt_char(SPHERO_CHARACTERISTIC_UUID, packet)
            return True
        except Exception as e:
            logger.error(f"Failed to send command: {str(e)}")
            return False

    def _calculate_checksum(self, data: bytes) -> int:
        """
        Calculate checksum for a command

        Args:
            data: Bytes to calculate checksum for (header + payload)

        Returns:
            Checksum byte
        """
        # Sum all bytes
        checksum_sum = sum(data) & 0xFF

        # Invert the bits
        return checksum_sum ^ 0xFF


# Example usage
async def main():
    sphero = SpheroBoltPlus()

    # Scan for devices
    devices = await sphero.scan_for_devices()

    if not devices:
        print("No Sphero BOLT devices found")
        return

    # Connect to the first device found
    print(f"Found {len(devices)} devices. Connecting to {devices[0]['name']}")
    connected = await sphero.connect(devices[0]['address'])

    if connected:
        # Drive the robot in a circle
        for heading in range(0, 360, 90):
            await sphero.drive(100, heading)
            await asyncio.sleep(1)

        # Set main LED to red
        await sphero.set_main_led(255, 0, 0)
        await asyncio.sleep(1)

        # Set main LED to green
        await sphero.set_main_led(0, 255, 0)
        await asyncio.sleep(1)

        # Set main LED to blue
        await sphero.set_main_led(0, 0, 255)
        await asyncio.sleep(1)

        # Disconnect
        await sphero.disconnect()

if __name__ == "__main__":
    asyncio.run(main())