import logging
import asyncio

class MettlerWorker:
    """Grab weight data from the Mettler loadcell and store realtime data as a local variable."""
    def __init__(self, ip, port=1026, frequency=100, logger=None):
        self.ip = ip
        self.port = port
        self.command = "SI\r\n"
        self.frequency = frequency
        self.is_running = False
        self.weight = float("nan")
        self.logger = logger or logging.getLogger("MettlerWorker") # 使用传入的 logger

    async def run(self):
        writer = None
        try:
            self.is_running = True
            self.logger.info(f"Opening connection to {self.ip}: {self.port}")
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(self.ip, self.port), 
                timeout=2.0
            )
            while self.is_running:
                self.logger.debug(f"send \"{self.command.strip()}\" to {self.ip}: {self.port}")
                writer.write(self.command.encode("ascii"))
                await writer.drain()
                response_bytes = await asyncio.wait_for(
                    reader.read(1024), 
                    timeout=2.0
                )
                response_str = response_bytes.decode("ascii")
                self.logger.debug(f"Get response: {response_str}")
                weight_data = self.parse_six1_response(response_str)
                self.weight = weight_data["gross"]
                await asyncio.sleep(1 / self.frequency)
        except (asyncio.TimeoutError, ConnectionRefusedError) as e:
            # 连接失败不应该让整个服务崩溃
            self.logger.error(f"Failed to connect to Mettler {self.ip}: {e}")
        except asyncio.CancelledError:
            self.logger.info("Mettler worker cancelled.")
        except Exception as e:
            self.logger.error(f"Mettler worker error: {e}", exc_info=True)
        finally:
            if writer: # make sure connection has been established once
                self.is_running = False
                self.logger.info("Closing Mettler connection.")
                writer.close()
                await writer.wait_closed()

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
            status_code = parts[1]
            gross_str = parts[2]
            unit = parts[3]

            return {
                "status": status_code, 
                "gross": float(gross_str),
                "unit": unit
            }
        except (IndexError, ValueError) as e:
            self.logger.error(f"错误：解析响应时出错: {e}\n原始响应: {response_str}")
            return None
        
    def stop(self):
        self.is_running = False