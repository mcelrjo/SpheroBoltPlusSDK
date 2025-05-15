from enum import Enum
import asyncio
import struct
import logging
import platform
import sys
from typing import List, Optional, Tuple, Dict, Any, Union

# Configure logging with more detailed format
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
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

    def __init__(self, debug: bool = False):
        self.device = None
        self.characteristic = None
        self.is_connected = False
        self.is_awake = False

        # Set debug mode if requested
        if debug:
            logging.getLogger("SpheroSDK").setLevel(logging.DEBUG)

        # Log system information
        self._log_system_info()

        # Import BLE library conditionally to support different platforms
        try:
            import bleak
            self.ble = bleak
            self.client = bleak.BleakClient
            self.scanner = bleak.BleakScanner
            logger.info("Successfully imported bleak library")
        except ImportError:
            logger.error("bleak library not found. Please install with: pip install bleak")
            raise ImportError("Required package 'bleak' not installed")

    def _log_system_info(self):
        """Log system information for debugging purposes"""
        logger.info(f"Operating System: {platform.system()} {platform.version()}")
        logger.info(f"Python Version: {sys.version}")
        logger.info(f"Platform: {platform.platform()}")
        if platform.system() == "Windows":
            logger.info("Windows detected - using Windows-specific BLE settings")

    async def scan_for_devices(self, timeout: int = 5, show_all: bool = False) -> List[Dict[str, Any]]:
        """
        Scan for nearby Sphero BOLT devices

        Args:
            timeout: Scan duration in seconds
            show_all: If True, log all discovered Bluetooth devices

        Returns:
            List of dictionaries containing device info
        """
        logger.info(f"Scanning for Sphero BOLT devices for {timeout} seconds...")

        # Use different scanning approach based on platform
        if platform.system() == "Windows":
            # On Windows, use keyword arguments specific to Windows BLE stack
            devices = await self.scanner.discover(
                timeout=timeout,
                return_adv=True,  # Return advertisement data
                scanning_mode="active"  # Active scanning mode
            )
        else:
            # Default scanning approach for other platforms
            devices = await self.scanner.discover(timeout=timeout)

        # Log all discovered devices if requested
        if show_all:
            logger.info(f"Found {len(devices)} Bluetooth devices in total:")
            for i, (address, device_info) in enumerate(
                    devices.items() if isinstance(devices, dict) else enumerate(devices)):
                # Handle different return formats between platforms
                if isinstance(devices, dict):  # Windows format
                    device_name = device_info.name if hasattr(device_info, 'name') else "Unknown"
                    device_address = address
                else:  # Other platforms
                    device_name = device_info.name if hasattr(device_info, 'name') else "Unknown"
                    device_address = device_info.address if hasattr(device_info, 'address') else str(device_info)

                logger.info(f"  {i + 1}. {device_name} ({device_address})")
                # Try to log UUIDs if available
                try:
                    if hasattr(device_info, 'metadata') and device_info.metadata:
                        logger.info(f"     UUIDs: {device_info.metadata.get('uuids', [])}")
                    elif hasattr(device_info, 'advertisement') and hasattr(device_info.advertisement, 'service_uuids'):
                        logger.info(f"     UUIDs: {device_info.advertisement.service_uuids}")
                except Exception as e:
                    logger.debug(f"Could not log UUIDs: {e}")

        sphero_devices = []

        # Process devices based on return format
        if isinstance(devices, dict):  # Windows format
            for address, device_info in devices.items():
                await self._process_device(address, device_info, sphero_devices)
        else:  # Other platforms format
            for device_info in devices:
                if hasattr(device_info, 'address'):
                    await self._process_device(device_info.address, device_info, sphero_devices)
                else:
                    logger.debug(f"Device has no address attribute: {device_info}")

        logger.info(f"Found {len(sphero_devices)} Sphero devices")
        return sorted(sphero_devices, key=lambda x: x.get('rssi', -100), reverse=True)

    async def _process_device(self, address, device_info, sphero_devices):
        """Helper method to process a discovered device"""
        # Extract device name
        device_name = None
        if hasattr(device_info, 'name'):
            device_name = device_info.name
        elif isinstance(device_info, dict) and 'name' in device_info:
            device_name = device_info['name']

        # Extract RSSI
        rssi = -100  # Default weak signal
        if hasattr(device_info, 'rssi'):
            rssi = device_info.rssi
        elif isinstance(device_info, dict) and 'rssi' in device_info:
            rssi = device_info['rssi']

        # Log device details for debugging
        logger.debug(f"Examining device: {address}")
        logger.debug(f"  Name: {device_name}")
        logger.debug(f"  RSSI: {rssi}")

        # Check if this is a Sphero device
        is_sphero = False

        # Check by name
        if device_name and device_name.startswith(BOLT_NAME_PREFIX):
            is_sphero = True
            logger.info(f"Found potential Sphero device by name: {device_name}")

        # Check by service UUID
        uuids = []
        try:
            # Try different ways to access service UUIDs
            if hasattr(device_info, 'metadata') and device_info.metadata:
                uuids = device_info.metadata.get("uuids", [])
            elif hasattr(device_info, 'advertisement') and hasattr(device_info.advertisement, 'service_uuids'):
                uuids = device_info.advertisement.service_uuids

            logger.debug(f"  UUIDs: {uuids}")
            if SPHERO_SERVICE_UUID.lower() in [str(uuid).lower() for uuid in uuids]:
                is_sphero = True
                logger.info(f"Found device advertising Sphero service: {device_name or address}")
        except Exception as e:
            logger.debug(f"Error checking service UUIDs: {e}")

        # For Windows, check advertisement data directly
        if platform.system() == "Windows" and hasattr(device_info, 'advertisement'):
            try:
                # Try to extract manufacturer data that might identify Sphero
                logger.debug("Checking advertisement data")
                if hasattr(device_info.advertisement, 'manufacturer_data'):
                    mfg_data = device_info.advertisement.manufacturer_data
                    logger.debug(f"  Manufacturer data: {mfg_data}")
                    # Sphero manufacturer ID could be checked here if known
            except Exception as e:
                logger.debug(f"Error checking advertisement data: {e}")

        # Add device if it's a Sphero or if we're desperate
        if is_sphero:
            # Add device with calculated distance (approximated from RSSI)
            sphero_devices.append({
                'address': address,
                'name': device_name or "Unknown Sphero",
                'rssi': rssi,
                'approximate_distance': self._calculate_distance_from_rssi(rssi)
            })

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
            # Convert address to string if it's not already
            address = str(address)

            # On Windows, specify adapter if there's an issue with the default
            if platform.system() == "Windows":
                # Try connection with Windows-specific parameters
                try:
                    logger.info("Attempting Windows-specific connection...")
                    self.device = self.client(address, timeout=20.0)
                except Exception as e:
                    logger.warning(f"Windows-specific connection failed: {str(e)}")
                    # Fallback to standard connection
                    self.device = self.client(address)
            else:
                self.device = self.client(address)

            # Connect with a longer timeout for reliable connection
            await self.device.connect(timeout=15.0)

            # On Windows, we need to discover services after connection
            services = await self.device.get_services()

            # Log available services and characteristics to help with debugging
            logger.debug("Available services:")
            for service in services:
                logger.debug(f"  Service: {service.uuid}")
                for char in service.characteristics:
                    logger.debug(f"    Characteristic: {char.uuid}, Properties: {char.properties}")

            # Try to find the characteristic
            self.characteristic = None
            for service in services:
                for char in service.characteristics:
                    if char.uuid.lower() == SPHERO_CHARACTERISTIC_UUID.lower():
                        self.characteristic = char
                        logger.info(f"Found API characteristic: {char.uuid}")
                        break

            # If characteristic wasn't found, try direct method
            if not self.characteristic:
                logger.warning("Characteristic not found in services enumeration, trying direct method")
                try:
                    # Try direct access method
                    self.characteristic = self.device.services.get_characteristic(SPHERO_CHARACTERISTIC_UUID)
                    if self.characteristic:
                        logger.info("Successfully found characteristic via direct method")
                except Exception as e:
                    logger.error(f"Error finding characteristic via direct method: {str(e)}")

                # Try by partial UUID
                if not self.characteristic:
                    logger.warning("Trying to find characteristic by partial UUID match")
                    short_uuid = SPHERO_CHARACTERISTIC_UUID.split('-')[0]
                    for service in services:
                        for char in service.characteristics:
                            if short_uuid in char.uuid:
                                self.characteristic = char
                                logger.info(f"Found API characteristic by partial match: {char.uuid}")
                                break

            if not self.characteristic:
                logger.error("Could not find API characteristic. Available characteristics:")
                for service in services:
                    for char in service.characteristics:
                        logger.error(f"  - {char.uuid}")
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
    # Create SDK with debug mode
    sphero = SpheroBoltPlus(debug=True)

    try:
        print("Scanning for Sphero BOLT devices...")
        print("(This may take 10+ seconds)")

        # Scan for devices with extended logging
        devices = await sphero.scan_for_devices(timeout=10, show_all=True)

        if not devices:
            print("\nNo Sphero BOLT devices found. Try the following troubleshooting steps:")
            print("1. Make sure your Sphero BOLT is charged and powered on")
            print("2. Make sure Bluetooth is enabled on your Surface Pro")
            print("3. Restart the Sphero BOLT by placing it in the charging cradle and removing it")
            print("4. Run this script with administrator privileges ('Run as administrator')")
            print("\nWould you like to try a more aggressive scan that might find the device?")
            choice = input("Enter 'y' to try desperate measures scan or any other key to exit: ")

            if choice.lower() == 'y':
                print("\nRunning desperate measures scan - looking for ANY Bluetooth device...")
                # Create a more aggressive scanning approach
                if platform.system() == "Windows":
                    # On Windows, try to use the raw BLE scanner
                    try:
                        from bleak import BleakScanner

                        print("Scanning for all Bluetooth devices...")
                        raw_devices = await BleakScanner.discover(timeout=10)

                        print(f"\nFound {len(raw_devices)} devices total")
                        for i, device in enumerate(raw_devices):
                            if hasattr(device, 'name'):
                                device_name = device.name or "Unknown"
                            else:
                                device_name = "Unknown"

                            if hasattr(device, 'address'):
                                device_addr = device.address
                            else:
                                device_addr = str(device)

                            print(f"{i + 1}. {device_name} ({device_addr})")

                        if raw_devices:
                            print("\nWould you like to try connecting to one of these devices?")
                            try_connect = input("Enter device number to try, or any other key to exit: ")

                            try:
                                device_idx = int(try_connect) - 1
                                if 0 <= device_idx < len(raw_devices):
                                    device = raw_devices[device_idx]
                                    if hasattr(device, 'address'):
                                        print(
                                            f"Attempting connection to {device.name or 'Unknown'} ({device.address})...")
                                        connected = await sphero.connect(device.address)
                                        if connected:
                                            print(
                                                "Connected! It seems to be working despite not being recognized as a Sphero!")
                                            # Run the demo
                                            await run_demo(sphero)
                                        else:
                                            print("Connection failed. This doesn't appear to be a Sphero BOLT.")
                                    else:
                                        print("Device doesn't have a valid address for connection.")
                            except ValueError:
                                print("Exiting without connecting.")
                    except Exception as e:
                        print(f"Error during desperate measures scan: {str(e)}")

            return

        # Show all found Sphero devices
        print(f"\nFound {len(devices)} Sphero BOLT devices:")
        for i, device in enumerate(devices):
            device_name = device.get('name', 'Unknown')
            device_rssi = device.get('rssi', 'Unknown')
            device_addr = device.get('address', 'Unknown')
            print(f"{i + 1}. {device_name} (Signal strength: {device_rssi} dBm, Address: {device_addr})")

        # Let user select device if multiple found
        selected_index = 0
        if len(devices) > 1:
            while True:
                try:
                    selected_index = int(input(f"Select device (1-{len(devices)}): ")) - 1
                    if 0 <= selected_index < len(devices):
                        break
                    print(f"Please enter a number between 1 and {len(devices)}")
                except ValueError:
                    print("Please enter a valid number")

        # Connect to the selected device
        selected_device = devices[selected_index]
        print(f"Connecting to {selected_device.get('name', 'Unknown')}...")
        connected = await sphero.connect(selected_device['address'])

        if connected:
            await run_demo(sphero)
        else:
            print("Failed to connect to Sphero BOLT.")
            print("Try the following:")
            print("1. Make sure the robot is charged")
            print("2. Run this script with administrator privileges")
            print("3. Try restarting your Bluetooth adapter")
    except Exception as e:
        print(f"An error occurred: {str(e)}")
        import traceback
        traceback.print_exc()


async def run_demo(sphero):
    """Run a demo sequence with the connected Sphero"""
    print("Successfully connected! Running demo...")

    # Drive the robot in a square
    for heading in [0, 90, 180, 270]:
        print(f"Driving with heading {heading}°...")
        await sphero.drive(100, heading)
        await asyncio.sleep(1.5)

    # Set main LED to different colors
    print("Changing LED colors...")
    for color in [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0)]:
        print(f"Setting color to RGB{color}...")
        await sphero.set_main_led(*color)
        await asyncio.sleep(1)

    # Disconnect
    print("Demo complete! Disconnecting...")
    await sphero.disconnect()


if __name__ == "__main__":
    # Handle keyboard interrupt gracefully
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nProgram interrupted by user. Exiting...")
    except Exception as e:
        print(f"Error: {str(e)}")
        import traceback

        traceback.print_exc()