from __future__ import annotations

from abc import ABC, abstractmethod
import math
import logging
from gateway import BaseGateway

# 1. 定义统一的接口
class SensorBase(ABC):
    @abstractmethod
    def get_value(self):
        pass

# 2. HTTP 设备的实现
class MettlerSensor(SensorBase):
    def __init__(self, gateway, params):
        self.gateway = gateway
        self.command = params["command_hex"]
        self.weight_position = params["weight_position"]
        self.logger = logging.getLogger(__name__)
        
    async def get_value(self) -> float | None:      
        raw_data = await self.gateway.exchange(self.command)
        return self.parse_six1_response(raw_data)
        
    def parse_six1_response(self, response_str):
        """
        解析 SI 命令的响应字符串。
        响应格式: S Sts Gross Unit
        """
        parts = response_str.strip().split()

        if len(parts) < 4 or parts[0] != 'S':
            self.logger.debug(f"错误：收到了意外的响应格式: {response_str}")
            return None
        
        try:
            gross_str = parts[self.weight_position]
            return float(gross_str)
           
        except (IndexError, ValueError) as e:
            self.logger.error(f"错误：解析响应时出错: {e}\n原始响应: {response_str}")
            return None

# 3. RS485 设备的实现
class RS485Sensor(SensorBase):

    def __init__(self, gateway, params):
        
        self.gateway = gateway
        self.address = params["address"]
        self.count = params["count"]
        self.dev_id = params["dev_id"]
        self.decimal_places = params.get("decimal_places", 3)
        
    async def get_value(self):
        # value 为当前称重重量，需要 parse modbus 的 response
        from pymodbus.pdu import ReadHoldingRegistersRequest
        request = ReadHoldingRegistersRequest(address=self.address, count=self.count, dev_id=self.dev_id)
        try:
            response = await self.gateway.exchange(request)
        except Exception as e:
            logging.error(f"Failed to read from RS485 sensor: {e}")
            return None
        weight = self.parse_modbus_response(response)
        return weight
    
    def parse_modbus_response(self, response):
        """解析 Modbus 响应，提取重量值。
        示例：
            response = [20, 0]
            低位寄存器： 20
            高位寄存器： 0
            组合为 32 位整数： 0 << 16 + 20 = 20
            考虑小数点： 20 / 10 ** decimal_places = 0.02 kg
        """
        if len(response) != 2:
            raise ValueError("Modbus response must have exactly 2 registers")
        low_reg_int = response[0]
        high_reg_int = response[1]
        raw_int = (high_reg_int << 16) + low_reg_int
        if raw_int & 0x00008000:
            raw_int -= 0xFFFFFFFF
        weight = raw_int / (10 ** self.decimal_places)
        return weight
    
class RotaryEncoderSensor(SensorBase):
    def __init__(self, 
                 gateway: BaseGateway,
                 params):
        self.gateway = gateway
        self.ppr = params["pulses_per_revolution"]
        self.diameter = params["diameter_mm"]

    def get_value(self):
        # value 为当前米数 (mm)
        # 计算米数：轮子周长 * steps / ppr
        steps = self.gateway.exchange(payload=None) # 这里直接调用网关的 exchange 获取当前计数
        return math.pi * self.diameter * steps / self.ppr
    
# 4. 业务代码（完全解耦）
async def test_sensors():
    config_path = r"C:\Users\zhengyang\Documents\GitHub\hepic_server\sensors_config.yaml"
    with open(config_path, "r", encoding="utf-8") as f:
        sensors_config = yaml.safe_load(f)

    gateways = {}
    for gateway_conf in sensors_config["gateways"]:
        print(f"创建网关 {gateway_conf['id']}，类型: {gateway_conf['type']}")
        if gateway_conf["type"] == "modbus":
            from gateway import ModbusGateway
            gateways[gateway_conf["id"]] = ModbusGateway(gateway_conf["port"], 
                                    baudrate = gateway_conf["baudrate"])
        elif gateway_conf["type"] == "tcp":
            from gateway import TCPGateway
            gateways[gateway_conf["id"]] = TCPGateway(gateway_conf["ip"], gateway_conf["port"])
        elif gateway_conf["type"] == "rotary_encoder":
            from gateway import GPIOEncoderGateway
            gateways[gateway_conf["id"]] = GPIOEncoderGateway(gateway_conf["pin_a"], gateway_conf["pin_b"])
            
    sensors = {}
    for sensor_conf in sensors_config["sensors"]:
        print(f"创建传感器 {sensor_conf['id']}，协议: {sensor_conf['protocol']}")
        if sensor_conf["protocol"] == "mettler":
            sensors[sensor_conf["id"]] = MettlerSensor(gateways[sensor_conf["gateway_id"]], sensor_conf["params"])
        elif sensor_conf["protocol"] == "modbus":
            sensors[sensor_conf["id"]] = RS485Sensor(gateways[sensor_conf["gateway_id"]], sensor_conf["params"])
        elif sensor_conf["protocol"] == "rotary_encoder":
            try:
                sensors[sensor_conf["id"]] = RotaryEncoderSensor(gateways[sensor_conf["gateway_id"]], sensor_conf["params"])
            except ImportError:
                logging.warning(f"无法创建 RotaryEncoderSensor {sensor_conf['id']}，gpiozero 未安装或不可用")

if __name__ == "__main__":
    import yaml
    import asyncio

    asyncio.run(test_sensors())

    
