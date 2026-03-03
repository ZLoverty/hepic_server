from __future__ import annotations

import asyncio
import aiohttp
from pymodbus.client import AsyncModbusSerialClient
from pymodbus.exceptions import ModbusException
import logging

class BaseGateway:
    """网关基类，定义统一的交互接口"""
    async def exchange(self, payload: any) -> any:
        """发送数据并接收回执"""
        raise NotImplementedError

# --- TCP 网关实现 ---
class TCPGateway(BaseGateway):
    def __init__(self, 
                 ip: str, 
                 port: int, 
                 timeout: int = 5):
        self.address = (ip, port)
        self.timeout = timeout
        self.reader: asyncio.StreamReader | None = None
        self.writer: asyncio.StreamWriter | None = None

    async def _ensure_connected(self):
        """内部方法：确保 TCP 长连接存活"""
        if self.writer and not self.writer.is_closing():
            return True
        try:
            self.reader, self.writer = await asyncio.open_connection(*self.address)
            print(f"已连接到 TCP 网关: {self.address}")
            return True
        except Exception as e:
            print(f"TCP 连接失败 {self.address}: {e}")
            return False

    async def exchange(self, command_hex: bytes) -> bytes | None:
        if not await self._ensure_connected():
            return None
        try:
            self.writer.write(command_hex)
            await self.writer.drain()
            # 异步读取，超时保护
            return await asyncio.wait_for(self.reader.read(1024), timeout=self.timeout)
        except Exception as e:
            print(f"TCP 通信异常: {e}")
            self.writer = None # 标记失效，触发下次重连
            return None

class ModbusGateway:
    def __init__(self, port: str, baudrate: int = 9600):
        # 初始化异步串口客户端
        self.client = AsyncModbusSerialClient(
            port=port,
            baudrate=baudrate,
            timeout=1
        )
        # 确保总线访问是串行的（RS485 特性）
        self._lock = asyncio.Lock()

    async def _ensure_connected(self) -> bool:
        """检查并保持长连接"""
        if self.client.connected:
            return True
        try:
            return await self.client.connect()
        except Exception as e:
            print(f"无法连接到串口 {self.client.comm_params.port}: {e}")
            return False

    async def exchange(self, request):
        """
        核心交换方法：
        接收一个 pymodbus 的 Request 对象，返回 Response 对象。
        """
        async with self._lock:
            if not await self._ensure_connected():
                return None

            try:
                # pymodbus 的 execute 方法会自动：
                # 1. 计算并添加 CRC
                # 2. 发送报文
                # 3. 接收报文并验证 CRC
                # 4. 解析为对应的 Response 对象
                response = await self.client.execute(False, request)
                
                if response.isError():
                    print(f"Modbus 业务错误: {response}")
                    return None
                    
                return response
            except ModbusException as e:
                print(f"Modbus 协议异常: {e}")
                return None
            except Exception as e:
                print(f"底层通信异常: {e}")
                self.client.close() # 发生严重异常时重置连接
                return None

class GPIOEncoderGateway:
    def __init__(self, pin_a, pin_b):
        from gpiozero import RotaryEncoder
        self.encoder = RotaryEncoder(pin_a, pin_b)
        # 这里初始化硬件中断 (以伪代码为例)
        # GPIO.add_event_detect(pin_a, GPIO.BOTH, callback=self._update)
        self.logger = logging.getLogger(__name__)

    async def exchange(self, request=None):
        """
        为了兼容框架，我们也叫 exchange。
        但它不发请求，而是直接返回内存里最新的计数。
        """
        return self.encoder.steps

async def test_tcp_gateway():
    gateway = TCPGateway("127.0.0.1", 1026)
    response = await gateway.exchange(bytes.fromhex("53490D0A"))
    if response:
        print(f"收到回执: {response.decode()}")
    else:
        print("未收到回执或通信失败")

async def test_modbus_gateway():
    gateway = ModbusGateway("/dev/cu.usbserial-110")
    # 构造一个 Modbus 请求（例如读取保持寄存器）
    from pymodbus.pdu import ReadHoldingRegistersRequest
    request = ReadHoldingRegistersRequest(address=0, count=2, dev_id=2)
    response = await gateway.exchange(request)
    
    if response:
        print(f"Modbus 响应: {response.registers}")
    else:
        print("Modbus 通信失败或无响应")

async def test_gpio_encoder_gateway():
    gateway = GPIOEncoderGateway(pin_a=17, pin_b=18)
    count = await gateway.exchange()
    print(f"GPIO 编码器计数: {count}")

if __name__ == "__main__":
    asyncio.run(test_tcp_gateway())
    # asyncio.run(test_modbus_gateway())