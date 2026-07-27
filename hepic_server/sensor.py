from __future__ import annotations

from abc import ABC, abstractmethod
import logging
import math
from pathlib import Path
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    try:
        from .gateway import BaseGateway
    except ImportError:
        from gateway import BaseGateway


class SensorBase(ABC):
    @abstractmethod
    async def get_value(self) -> float | None:
        raise NotImplementedError

    async def zero(self) -> bool:
        """Trigger a hardware-level zero/tare on the instrument itself. Sensors that
        don't support an instrument-level zero return False; callers should treat
        that as "not supported" rather than a transient failure."""
        return False

    def is_zeroable(self) -> bool:
        """Cheap, side-effect-free capability check — unlike zero(), this never
        talks to hardware. It's the source of truth for the "zeroable" flag
        reported to clients, so it must reflect actual per-instance config
        (e.g. an RS485 sensor with no zero register configured is not zeroable),
        not just "this class knows how to zero in general"."""
        return False


class MettlerSensor(SensorBase):
    def __init__(self, gateway: "BaseGateway", params: dict[str, Any]):
        self.gateway = gateway
        command_hex = params.get("command_hex")
        if command_hex:
            self.command = bytes.fromhex(str(command_hex))
        else:
            self.command = b"SI\r\n"
        zero_command_hex = params.get("zero_command_hex")
        if zero_command_hex:
            self.zero_command = bytes.fromhex(str(zero_command_hex))
        else:
            self.zero_command = b"Z\r\n"
        self.weight_position = params.get("weight_position", 2)
        self.logger = logging.getLogger(__name__)

    async def zero(self) -> bool:
        self.logger.debug(f"Sending zero command to Mettler sensor: {self.zero_command.hex()}")
        raw_data = await self.gateway.exchange(self.zero_command)
        if raw_data is None:
            self.logger.warning("No response received from Mettler zero command")
            return False
        response_str = raw_data.decode("ascii", errors="ignore") if isinstance(raw_data, bytes) else str(raw_data)
        self.logger.debug(f"Received response from Mettler zero: {raw_data!r}")
        # MT-SICS "Z" reply: "Z A" = zero set, "Z I" = not executable right now,
        # "Z L" = out of zero-setting range. Only "Z A" counts as success.
        first_line = response_str.strip().splitlines()[0] if response_str.strip() else ""
        return first_line.upper().startswith("Z A")

    def is_zeroable(self) -> bool:
        return True

    async def get_value(self) -> float | None:
        self.logger.debug(f"Sending command to Mettler sensor: {self.command.hex()}")
        raw_data = await self.gateway.exchange(self.command)

        if raw_data is None:
            self.logger.warning("No response received from Mettler sensor")
            return None
        if isinstance(raw_data, bytes):
            response_str = raw_data.decode("ascii", errors="ignore")
        else:
            response_str = str(raw_data)
        self.logger.debug(f"Received response from Mettler sensor: {raw_data!r}")

        return self.parse_six1_response(response_str)

    def parse_six1_response(self, response_str: str) -> float | None:
        parts = response_str.strip().split()
        if len(parts) < 4 or parts[0] != "S":
            self.logger.debug(f"Unexpected Mettler response: {response_str!r}")
            return None
        try:
            return float(parts[self.weight_position]) * 9.81
        except (IndexError, ValueError) as e:
            self.logger.error(f"Failed to parse Mettler response: {e}; raw={response_str!r}")
            return None


class RS485Sensor(SensorBase):
    def __init__(self, gateway: "BaseGateway", params: dict[str, Any]):
        self.gateway = gateway
        self.address = params["address"]
        self.count = params["count"]
        self.dev_id = params["dev_id"]
        self.decimal_places = params.get("decimal_places", 3)
        self.zero_address = params.get("zero_address")
        self.zero_value = params.get("zero_value", 1)
        self.logger = logging.getLogger(__name__)

    async def zero(self) -> bool:
        if self.zero_address is None:
            self.logger.warning("RS485 sensor has no zero_address configured; cannot zero at the instrument.")
            return False

        from pymodbus.pdu.register_message import WriteSingleRegisterRequest

        request = WriteSingleRegisterRequest(
            address=self.zero_address,
            registers=[self.zero_value],
            dev_id=self.dev_id,
        )
        try:
            response = await self.gateway.exchange(request)
            return response is not None
        except Exception as e:
            self.logger.error(f"Failed to zero RS485 sensor: {e}")
            return False

    def is_zeroable(self) -> bool:
        return self.zero_address is not None

    async def get_value(self) -> float | None:
        from pymodbus.pdu import ReadHoldingRegistersRequest

        request = ReadHoldingRegistersRequest(
            address=self.address,
            count=self.count,
            dev_id=self.dev_id,
        )
        try:
            response = await self.gateway.exchange(request)
            if response is None:
                return None
            registers = getattr(response, "registers", None)
            if not registers:
                return None
            return self.parse_modbus_registers(registers) * 9.81
        except Exception as e:
            self.logger.error(f"Failed to read RS485 sensor: {e}")
            return None

    def parse_modbus_registers(self, registers: list[int]) -> float:
        if len(registers) < 2:
            raise ValueError("Modbus response must contain at least 2 registers")
        low_reg_int = int(registers[0])
        high_reg_int = int(registers[1])
        raw_int = (high_reg_int << 16) + low_reg_int
        if raw_int & 0x80000000:
            raw_int -= 0x100000000
        return raw_int / (10**self.decimal_places)


class RotaryEncoderSensor(SensorBase):
    """The encoder itself has no zero/tare capability (it's a plain GPIO step
    counter), so zeroing here means the same thing it used to mean client-side:
    remember the current reading as an offset and subtract it going forward.
    The difference is this offset now lives once on the server instead of once
    per connected client, so every client sees the same zeroed value."""

    def __init__(self, gateway: "BaseGateway", params: dict[str, Any]):
        self.gateway = gateway
        self.ppr = params["pulses_per_revolution"]
        self.diameter = params["diameter_mm"]
        self.offset = 0.0
        self.logger = logging.getLogger(__name__)

    def _steps_to_mm(self, steps: float) -> float:
        return math.pi * self.diameter * float(steps) / self.ppr

    async def get_value(self) -> float | None:
        steps = await self.gateway.exchange(payload=None)
        if steps is None:
            return None
        return self._steps_to_mm(steps) - self.offset

    async def zero(self) -> bool:
        steps = await self.gateway.exchange(payload=None)
        if steps is None:
            self.logger.warning("No reading available from rotary encoder; cannot zero.")
            return False
        self.offset = self._steps_to_mm(steps)
        return True

    def is_zeroable(self) -> bool:
        return True


def build_gateways(config: dict[str, Any]) -> dict[str, "BaseGateway"]:
    try:
        from .gateway import GPIOEncoderGateway, ModbusGateway, TCPGateway
    except ImportError:
        from gateway import GPIOEncoderGateway, ModbusGateway, TCPGateway

    gateways: dict[str, "BaseGateway"] = {}
    for gateway_conf in config.get("gateways", []):
        gateway_type = gateway_conf["type"]
        gateway_id = gateway_conf["id"]
        if gateway_type == "modbus":
            gateways[gateway_id] = ModbusGateway(
                gateway_conf["port"],
                baudrate=gateway_conf.get("baudrate", 9600),
            )
        elif gateway_type == "tcp":
            gateways[gateway_id] = TCPGateway(
                gateway_conf["ip"],
                gateway_conf["port"],
                timeout=gateway_conf.get("timeout", 5),
            )
        elif gateway_type == "rotary_encoder":
            gateways[gateway_id] = GPIOEncoderGateway(
                gateway_conf["pin_a"],
                gateway_conf["pin_b"],
            )
        else:
            raise ValueError(f"Unsupported gateway type: {gateway_type}")
    return gateways


def build_sensors(
    config: dict[str, Any],
    gateways: dict[str, "BaseGateway"],
) -> dict[str, SensorBase]:
    sensors: dict[str, SensorBase] = {}
    for sensor_conf in config.get("sensors", []):
        sensor_id = sensor_conf["id"]
        protocol = sensor_conf["protocol"]
        gateway_id = sensor_conf["gateway_id"]
        params = sensor_conf.get("params", {})
        if gateway_id not in gateways:
            raise ValueError(f"Sensor {sensor_id} references unknown gateway: {gateway_id}")

        gateway = gateways[gateway_id]
        if protocol == "mettler":
            sensors[sensor_id] = MettlerSensor(gateway, params)
        elif protocol == "modbus":
            sensors[sensor_id] = RS485Sensor(gateway, params)
        elif protocol == "rotary_encoder":
            sensors[sensor_id] = RotaryEncoderSensor(gateway, params)
        else:
            raise ValueError(f"Unsupported sensor protocol: {protocol}")
    return sensors


def load_sensors_from_yaml(config_path: str | Path) -> dict[str, SensorBase]:
    try:
        import yaml
    except ModuleNotFoundError as e:
        raise RuntimeError("PyYAML is required to load sensors_config.yaml. Install with: pip install PyYAML") from e

    with open(Path(config_path), "r", encoding="utf-8") as f:
        sensors_config = yaml.safe_load(f) or {}
    gateways = build_gateways(sensors_config)
    return build_sensors(sensors_config, gateways)
