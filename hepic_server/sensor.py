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


class MettlerSensor(SensorBase):
    def __init__(self, gateway: "BaseGateway", params: dict[str, Any]):
        self.gateway = gateway
        self.command = bytes.fromhex(params["command_hex"])
        self.weight_position = params.get("weight_position", 2)
        self.logger = logging.getLogger(__name__)

    async def get_value(self) -> float | None:
        raw_data = await self.gateway.exchange(self.command)
        if raw_data is None:
            return None
        if isinstance(raw_data, bytes):
            response_str = raw_data.decode("ascii", errors="ignore")
        else:
            response_str = str(raw_data)
        return self.parse_six1_response(response_str)

    def parse_six1_response(self, response_str: str) -> float | None:
        parts = response_str.strip().split()
        if len(parts) < 4 or parts[0] != "S":
            self.logger.debug(f"Unexpected Mettler response: {response_str!r}")
            return None
        try:
            return float(parts[self.weight_position])
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
        self.logger = logging.getLogger(__name__)

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
            return self.parse_modbus_registers(registers)
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
    def __init__(self, gateway: "BaseGateway", params: dict[str, Any]):
        self.gateway = gateway
        self.ppr = params["pulses_per_revolution"]
        self.diameter = params["diameter_mm"]

    async def get_value(self) -> float | None:
        steps = await self.gateway.exchange(payload=None)
        if steps is None:
            return None
        return math.pi * self.diameter * float(steps) / self.ppr


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
