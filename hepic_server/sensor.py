from __future__ import annotations

from abc import ABC, abstractmethod
import math
import asyncio
import logging
from gateway import BaseGateway

# 1. 定义统一的接口
class SensorBase(ABC):
    @abstractmethod
    def get_value(self):
        pass

# 2. HTTP 设备的实现
class TCPSensor(SensorBase):
    def __init__(self, gateway, params):
        self.gateway = gateway
        self.command = params["command"]
        self.weight_position = params["weight_position"]
        self.reader: asyncio.StreamReader | None = None
        self.writer: asyncio.StreamWriter | None = None
        self.logger = logging.getLogger(__name__)
        
    def get_value(self) -> float | None:      
        raw_data = await self.gateway.exchange(self.command)
        
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
    def __init__(self, port):
        self.port = port # 比如 /dev/ttyUSB0
    def get_value(self):
        # 伪代码：通过 pymodbus 读取
        # return client.read_input_registers(0).registers[0] / 10
        return 26.2

class RotaryEncoderSensor(SensorBase):
    def __init__(self, 
                 gateway: BaseGateway,
                 params):
        self.pin_a = pin_a
        self.pin_b = pin_b
        self.ppr = pulses_per_revolution
        self.diameter = diameter
        from gpiozero import RotaryEncoder
        self.encoder = RotaryEncoder(pin_a, pin_b, max_steps=0)

    def get_value(self):
        # value 为当前米数
        # 计算米数：轮子周长 * steps / ppr
        return math.pi * self.diameter * self.encoder.steps / self.ppr
    
# 4. 业务代码（完全解耦）
def monitor_device(sensor: SensorBase):
    temp = sensor.get_value()
    print(f"当前传感器值为: {temp}")

if __name__ == "__main__":
    import yaml
    import json
    with open("../sensors_config.yaml", "r") as f:
        sensors = yaml.safe_load(f)
    print(json.dumps(sensors, indent=2))
    for sensor_conf in sensors["sensors"]:
        print(f"创建传感器 {sensor_conf['id']}，协议: {sensor_conf['protocol']}")
