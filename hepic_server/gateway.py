from __future__ import annotations

import asyncio
import logging
from typing import Any

from pymodbus.client import AsyncModbusSerialClient
from pymodbus.exceptions import ModbusException

logger = logging.getLogger(__name__)


class BaseGateway:
    async def exchange(self, payload: Any) -> Any:
        raise NotImplementedError


class TCPGateway(BaseGateway):
    def __init__(self, ip: str, port: int, timeout: int = 5):
        self.address = (ip, port)
        self.timeout = timeout
        self.reader: asyncio.StreamReader | None = None
        self.writer: asyncio.StreamWriter | None = None

    async def _ensure_connected(self) -> bool:
        if self.writer and not self.writer.is_closing():
            return True
        try:
            self.reader, self.writer = await asyncio.open_connection(*self.address)
            logger.info(f"Connected to TCP gateway: {self.address}")
            return True
        except Exception as e:
            logger.error(f"TCP connection failed {self.address}: {e}")
            return False

    async def exchange(self, command_hex: bytes) -> bytes | None:
        if not await self._ensure_connected():
            return None
        try:
            command_hex = b"SI\r\n"
            self.writer.write(command_hex)
            await self.writer.drain()
            return await asyncio.wait_for(self.reader.read(1024), timeout=self.timeout)
        except Exception as e:
            logger.error(f"TCP communication error: {e}")
            self.writer = None
            return None


class ModbusGateway(BaseGateway):
    def __init__(self, port: str, baudrate: int = 9600):
        self.client = AsyncModbusSerialClient(
            port=port,
            baudrate=baudrate,
            timeout=1,
        )
        self._lock = asyncio.Lock()

    async def _ensure_connected(self) -> bool:
        if self.client.connected:
            return True
        try:
            return await self.client.connect()
        except Exception as e:
            logger.error(f"Unable to connect serial port {self.client.comm_params.port}: {e}")
            return False

    async def exchange(self, request: Any):
        async with self._lock:
            if not await self._ensure_connected():
                return None
            try:
                response = await self.client.execute(False, request)
                if response.isError():
                    logger.error(f"Modbus business error: {response}")
                    return None
                return response
            except ModbusException as e:
                logger.error(f"Modbus protocol error: {e}")
                return None
            except Exception as e:
                logger.error(f"Transport layer communication error: {e}")
                self.client.close()
                return None


class GPIOEncoderGateway(BaseGateway):
    def __init__(self, pin_a: int, pin_b: int):
        self.logger = logger
        self.encoder = None
        try:
            from gpiozero import RotaryEncoder
        except ImportError as e:
            self.logger.error(f"Failed to import gpiozero: {e}")
            return
        self.encoder = RotaryEncoder(pin_a, pin_b, max_steps=0)  # max_steps=0 for unlimited counting

    async def exchange(self, payload: Any = None):
        if self.encoder is None:
            self.logger.debug("GPIO encoder unavailable, returning None")
            return None
        return self.encoder.steps


async def test_tcp_gateway():
    gateway = TCPGateway("127.0.0.1", 1026)
    response = await gateway.exchange(bytes.fromhex("53490D0A"))
    if response:
        logger.info(f"Received response: {response.decode(errors='ignore')}")
    else:
        logger.warning("No response or communication failure")


async def test_modbus_gateway():
    gateway = ModbusGateway("/dev/cu.usbserial-110")
    from pymodbus.pdu import ReadHoldingRegistersRequest

    request = ReadHoldingRegistersRequest(address=0, count=2, dev_id=2)
    response = await gateway.exchange(request)
    if response:
        logger.info(f"Modbus response: {response.registers}")
    else:
        logger.warning("Modbus communication failure or no response")


async def test_gpio_encoder_gateway():
    gateway = GPIOEncoderGateway(pin_a=17, pin_b=18)
    count = await gateway.exchange()
    logger.info(f"GPIO encoder count: {count}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(test_tcp_gateway())
