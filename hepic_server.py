import asyncio
import json
import random
import logging
import logging.handlers
import signal
import sys
from pathlib import Path
import numpy as np
import argparse

class PiServer:
    """
    一个健壮的、可作为服务运行的异步TCP服务器。
    它从配置文件加载设置，使用专业的日志系统，并能优雅地处理关闭信号。
    """
    def __init__(self, config_path, test_mode=False):
        self.config = self._load_config(config_path)
        self.logger = self._setup_logging()
        self.test_mode = test_mode # if test_mode is True, generate random numbers instead of read data from sensors
        self.mettler_ip = self.config.get("mettler_ip") # loadcell IP
        self.is_running = False
        self.server = None
        self.tasks = set()

        # initiate workers that communicates with sensors and PC
        self.mettler_worker = MettlerWorker(self.mettler_ip, logger=self.logger)
        self.meter_count_worker = MeterCountWorker()

    def _load_config(self, path):
        """加载 JSON 配置文件"""
        config_file = Path(path).expanduser()
        if not config_file.is_file():
            self.logger.error(f"错误：配置文件 {path} 未找到！", file=sys.stderr)
            sys.exit(1)
        with open(config_file, 'r', encoding='utf-8') as f:
            return json.load(f)

    def _setup_logging(self):
        """配置日志系统，同时输出到控制台和可轮换的文件"""
        logger = logging.getLogger("TCPServer")
        logger.setLevel(self.config.get("log_level", "INFO").upper())
        
        # 格式化
        formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        
        # 控制台输出
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(formatter)
        logger.addHandler(stream_handler)
        
        return logger

    async def _handle_client(self, reader, writer):
        
        peer_addr = writer.get_extra_info('peername')

        self.logger.info(f"accepting new link from {peer_addr}")

        shutdown_signal = asyncio.Future()

        async def send_loop():
            """周期性地发送数据给客户端"""

            while not shutdown_signal.done():
                try:
                    if self.test_mode: 
                        # generate random data
                        weight = 2 + random.uniform(-.2, .2)
                        meter = 2 + random.uniform(-.2, .2)
                    else:
                        # read real data
                        weight = self.mettler_worker.weight
                        meter = self.meter_count_worker.meter_count
             
                    message = {
                        "extrusion_force": weight * 9.8,
                        "meter_count": meter
                    }
                    data_to_send = json.dumps(message).encode("utf-8") + b'\n'
                    self.logger.debug(f"sending to {peer_addr} -> {message}")
                    writer.write(data_to_send)
                    await writer.drain()             
                    await asyncio.sleep(self.config.get("send_delay", 0.01))

                except (ConnectionResetError, BrokenPipeError) as e:
                    self.logger.warning(f"disconnect from {peer_addr}: {e}")
                    if not shutdown_signal.done():
                        shutdown_signal.set_result(True)
                except KeyboardInterrupt:
                    self.logger.error("\n程序被用户中断。")
                    sys.exit(1)
                except Exception as e:
                    self.logger.error(f"unknow error sending to {peer_addr}: {e}", exc_info=True)
                    if not shutdown_signal.done():
                        shutdown_signal.set_result(True)

        async def receive_loop():
            """从客户端接收数据"""
            try:
                while not shutdown_signal.done():
                        data = await reader.read(1024)
                        if not data:
                            self.logger.info(f"client {peer_addr} has disconnected")
                            if not shutdown_signal.done():
                                shutdown_signal.set_result(True)
                        message = data.decode().strip()
                        self.logger.info(f"received from {peer_addr}: {message!r}")
            except ConnectionResetError:
                # 这是关键：捕获错误
                self.logger.error(f"Client {peer_addr} forcibly closed connection (Connection reset).")
            except Exception as e:
                self.logger.error(f"error when receiving from {peer_addr}: {e}", exc_info=True)
                if not shutdown_signal.done():
                    shutdown_signal.set_result(True)

        send_task = asyncio.create_task(send_loop())
        receive_task = asyncio.create_task(receive_loop())
        self.tasks.add(send_task)
        self.tasks.add(receive_task)

        await shutdown_signal
        
        send_task.cancel()
        receive_task.cancel()
        self.tasks.remove(send_task)
        self.tasks.remove(receive_task)
        
        self.logger.info(f"close connection from {peer_addr}")
        writer.close()
        await writer.wait_closed()

    async def _shutdown(self, sig):
        """优雅地关闭服务器"""
        self.logger.info(f"receive close signal: {sig.name}. closing...")
        
        # 停止接受新连接
        if self.server:
            self.server.close()
            await self.server.wait_closed()

        # 2. 主动停止所有 worker
        self.logger.info("Stopping internal workers...")
        if hasattr(self, 'mettler_task'): # 检查任务是否存在
            self.mettler_worker.stop()
            # self.meter_count_worker.stop() # 将来也停止它
            
            # 等待 worker 任务完成
            try:
                await asyncio.wait_for(self.mettler_task, timeout=2.0)
            except asyncio.TimeoutError:
                self.logger.warning("Mettler worker did not stop in time, cancelling.")
                self.mettler_task.cancel()
            except Exception as e:
                self.logger.error(f"Error during worker shutdown: {e}")

        self.logger.info("Shutdown complete.")

    async def run(self):
        """启动服务器并监听信号"""
        loop = asyncio.get_running_loop()
        # 为 SIGINT (Ctrl+C) 和 SIGTERM (来自 systemd) 添加信号处理器
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, lambda s=sig: asyncio.create_task(self._shutdown(s)))

        self.is_running = True

        if not self.test_mode:
            mettler_task = asyncio.create_task(self.mettler_worker.run())
            
            # self.meter_count_worker.run()

        shutdown_signal = asyncio.Future()

        host = self.config.get("host", "0.0.0.0")
        port = self.config.get("port", 10001)

        try:
            self.server = await asyncio.start_server(self._handle_client, host, port)
            addrs = ', '.join(str(sock.getsockname()) for sock in self.server.sockets)
            self.logger.info(f"server start listening {addrs}")
            await self.server.serve_forever()

        except (ConnectionResetError, BrokenPipeError) as e:
            self.logger.warning(f"Connection error.")
            if not shutdown_signal.done():
                shutdown_signal.set_result(True)

        except asyncio.CancelledError:
            # 这是 _shutdown 触发的正常关闭
            self.logger.debug(f"Server shutdown.")
            if not shutdown_signal.done():
                shutdown_signal.set_result(True)
            raise # 重新引发 CancelledError 很重要
        
        except Exception as e:
            self.logger.error(f"unknow error, server shut down.")
            if not shutdown_signal.done():
                shutdown_signal.set_result(True)

class MettlerWorker:
    """Grab weight data from the Mettler loadcell and store realtime data as a local variable."""
    def __init__(self, ip, port=1026, frequency=100, logger=None):
        self.ip = ip
        self.port = port
        self.command = "SI\r\n"
        self.frequency = frequency
        self.is_running = False
        self.weight = np.nan
        self.logger = logger or logging.getLogger("MettlerWorker") # 使用传入的 logger

    async def run(self):
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


            
if __name__ == "__main__":
    # 假设配置文件与脚本在同一目录下

    parser = argparse.ArgumentParser(description="Pi data server TCP")

    parser.add_argument("config_file", type=str, help="path to the config json file.")
    parser.add_argument("-t", "--test_mode", help="test mode switch", action="store_true")
    args = parser.parse_args()
    
    server_app = PiServer(args.config_file, test_mode=args.test_mode)
    try:
        asyncio.run(server_app.run())
    except (KeyboardInterrupt, SystemExit):
        server_app.logger.info("program closed")
