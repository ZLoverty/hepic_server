import sys
from pathlib import Path
sys.path.append(str(Path(__file__).resolve().parent))
from workers import MettlerWorker, MeterCountWorker
import asyncio
import json
import random
import logging
import signal
import argparse

import threading

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
        self.pin_a = self.config.get("pin_a")
        self.pin_b = self.config.get("pin_b")
        self.logger.info(f"DEBUG: Loaded Pin A: {self.pin_a}, Type: {type(self.pin_a)}")
        self.logger.info(f"DEBUG: Loaded Pin B: {self.pin_b}, Type: {type(self.pin_b)}")
        self.is_running = False
        self.server = None
        self.client_tasks = set()
        self.METER_COUNT_WORKER_AVAILABLE = False

        # initiate workers that communicates with sensors and PC
        if not self.test_mode:
            self.mettler_worker = MettlerWorker(self.mettler_ip, logger=self.logger)
            self.meter_count_worker = MeterCountWorker(self.pin_a, self.pin_b, print=True)
            self.thread = threading.Thread(target=self.meter_count_worker.run)
            self.thread.daemon = True # 设为守护线程

    def _load_config(self, path):
        """加载 JSON 配置文件"""
        config_file = Path(path).expanduser()
        if not config_file.is_file():
            print(f"错误：配置文件 {path} 未找到！", file=sys.stderr)
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

        current_task = asyncio.current_task()
        self.client_tasks.add(current_task)

        async def send_loop():
            """周期性地发送数据给客户端"""
            try:
                while True:
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
                raise
            except KeyboardInterrupt:
                self.logger.error("\n程序被用户中断。")
                raise
            except asyncio.CancelledError:
                self.logger.info("Send loop cancelled.")
                raise
            except Exception as e:
                self.logger.error(f"unknow error sending to {peer_addr}: {e}", exc_info=True)
                raise

        async def receive_loop():
            """从客户端接收数据"""
            try:
                while True:
                    data = await reader.read(1024)
                    if not data:
                        self.logger.info(f"client {peer_addr} has disconnected")
                        break
                    message = data.decode().strip()
                    self.logger.info(f"received from {peer_addr}: {message!r}")
            except ConnectionResetError:
                # 这是关键：捕获错误
                self.logger.error(f"Client {peer_addr} forcibly closed connection (Connection reset).")
                raise
            except asyncio.CancelledError:
                self.logger.info("Receive loop cancelled.")
                raise
            except Exception as e:
                self.logger.error(f"error when receiving from {peer_addr}: {e}", exc_info=True)
                raise


        try:
            send_task = asyncio.create_task(send_loop())
            receive_task = asyncio.create_task(receive_loop())

            done, pending = await asyncio.wait(
                [send_task, receive_task],
                return_when=asyncio.FIRST_COMPLETED
            )
        
        except asyncio.CancelledError:
            self.logger.info(f"Connection handler for {peer_addr} cancelled.")
        
        except Exception as e:
            self.logger.error(f"Handler exception: {e}")

        finally:
            for task in [send_task, receive_task]:
                if task and not task.done():
                    task.cancel()
            
            # wait for tasks to complete
            if send_task or receive_task:
                await asyncio.gather(send_task, receive_task, return_exceptions=True)

            # close socket
            self.logger.info("Close socket ...")
            writer.close()
            try:
                await writer.wait_closed()
            except:
                pass

            self.client_tasks.discard(current_task)

    async def _shutdown(self, sig):
        """优雅地关闭服务器"""
        self.logger.info(f"receive close signal: {sig.name}. closing...")
        
        # 停止接受新连接
        if self.server:
            self.logger.info("closing server")
            self.server.close()
            self.logger.info("waiting server to close")
            # await asyncio.wait_for(self.server.wait_closed(), timeout=2.0)

        # 2. 【新增】取消所有活跃的客户端连接任务
        if self.client_tasks:
            self.logger.info(f"Cancelling {len(self.client_tasks)} active client tasks...")
            for task in list(self.client_tasks):
                task.cancel()
            # 等待它们响应取消并清理
            await asyncio.gather(*self.client_tasks, return_exceptions=True)

        # 2. 主动停止所有 worker
        self.logger.info("Stopping internal workers...")
        if hasattr(self, 'mettler_task'): # 检查任务是否存在
            self.logger.info("Mettler task stopped.")
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
        asyncio.get_running_loop().stop()

    async def run(self):
        """启动服务器并监听信号"""

        if self.METER_COUNT_WORKER_AVAILABLE:
            self.thread.start()
        
        loop = asyncio.get_running_loop()
        # 为 SIGINT (Ctrl+C) 和 SIGTERM (来自 systemd) 添加信号处理器
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, lambda s=sig: asyncio.create_task(self._shutdown(s)))

        if not self.test_mode:
            self.mettler_task = asyncio.create_task(self.mettler_worker.run())
            
            # self.meter_count_worker.run()

        host = self.config.get("host", "0.0.0.0")
        port = self.config.get("port", 10001)

        try:
            self.server = await asyncio.start_server(self._handle_client, host, port)
            addrs = ', '.join(str(sock.getsockname()) for sock in self.server.sockets)
            self.logger.info(f"server start listening {addrs}")
            await self.server.serve_forever()
        except asyncio.CancelledError:
            pass

        except Exception as e:
            self.logger.error(f"Server exception: {e}")
        finally:
            # 确保最后 server 也是关闭的
            if self.server:
                self.server.close()

def main():
    from importlib.metadata import version, PackageNotFoundError
    package_name = "hepic_server"
    try:
        # 只有当包被 pip install (包括 pip install -e .) 后才能读到
        __version__ = version(package_name)
    except PackageNotFoundError:
        # 如果是直接 python server.py 运行且未安装，给个默认值
        __version__ = "dev-local"

    parser = argparse.ArgumentParser(description="Pi data server TCP")
    parser.add_argument("config_file", type=str, help="path to the config json file.")
    parser.add_argument("-t", "--test_mode", help="test mode switch", action="store_true")
    parser.add_argument("-v", "--version", action="version", version=f"hepic_server version {__version__}")
    args = parser.parse_args()
    
    server_app = PiServer(args.config_file, test_mode=args.test_mode)

    try:
        asyncio.run(server_app.run())
    except (KeyboardInterrupt, SystemExit):
        server_app.logger.info("program closed")

if __name__ == "__main__":
    main()
