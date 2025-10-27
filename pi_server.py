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
import numpy as np

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
        self.message_queue = asyncio.Queue()

        # initiate workers that communicates with sensors and PC
        self.mettler_worker = MettlerWorker(self.mettler_ip)
        self.meter_count_worker = MeterCountWorker()
        self.mettler_worker.run()
        

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
        
        # 文件输出 (如果配置了)
        log_file = self.config.get("log_file")
        if log_file:
            # 使用 RotatingFileHandler 实现日志文件自动分割
            # 10MB一个文件，最多保留5个
            log_file_path = Path(log_file).expanduser().resolve().parent
            if not log_file_path.exists():
                log_file_path.mkdir()
                
            file_handler = logging.handlers.RotatingFileHandler(
                log_file, maxBytes=10*1024*1024, backupCount=5, encoding='utf-8')
            file_handler.setFormatter(formatter)
            logger.addHandler(file_handler)
        return logger

    async def _handle_client(self, reader, writer):
        
        addr = writer.get_extra_info('peername')
        print(f"接受来自 {addr} 的新连接")
        self.logger.info(f"accepting new link from {addr}")

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
                    
                    self.logger.debug(f"sending to {addr} -> {message}")
                    writer.write(data_to_send)
                    await writer.drain()             
                    await asyncio.sleep(self.config.get("send_delay", 0.01))

                except (ConnectionResetError, BrokenPipeError) as e:
                    self.logger.warning(f"disconnect from {addr}: {e}")
                    if not shutdown_signal.done():
                        shutdown_signal.set_result(True)
                except KeyboardInterrupt:
                    print("\n程序被用户中断。")
                    sys.exit(1)
                except Exception as e:
                    self.logger.error(f"unknow error sending to {addr}: {e}", exc_info=True)
                    if not shutdown_signal.done():
                        shutdown_signal.set_result(True)

        async def receive_loop():
            """从客户端接收数据"""
            try:
                while not shutdown_signal.done():
                        data = await reader.read(1024)
                        if not data:
                            self.logger.info(f"client {addr} has disconnected")
                            if not shutdown_signal.done():
                                shutdown_signal.set_result(True)
                        message = data.decode().strip()
                        self.logger.info(f"received from {addr}: {message!r}")
            except ConnectionResetError:
                # 这是关键：捕获错误
                print(f"Client {addr} forcibly closed connection (Connection reset).")
            except Exception as e:
                self.logger.error(f"error when receiving from {addr}: {e}", exc_info=True)
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
        
        self.logger.info(f"close connection from {addr}")
        writer.close()
        await writer.wait_closed()

    async def _shutdown(self, sig):
        """优雅地关闭服务器"""
        self.logger.info(f"receive close signal: {sig.name}. closing...")
        
        # 停止接受新连接
        if self.server:
            self.server.close()
            await self.server.wait_closed()

        send_task = asyncio.create_task(send(writer))
        recv_task = asyncio.create_task(recv(reader))
        proc_task = asyncio.create_task(proc())

        await asyncio.gather(send_task, recv_task, proc_task)

    async def run(self):
        """启动服务器并监听信号"""
        loop = asyncio.get_running_loop()
        # 为 SIGINT (Ctrl+C) 和 SIGTERM (来自 systemd) 添加信号处理器
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, lambda s=sig: asyncio.create_task(self._shutdown(s)))

        if not self.test_mode:
            self.mettler_worker.run()
            self.meter_count_worker.run()

        host = self.config.get("host", "0.0.0.0")
        port = self.config.get("port", 10001)

        try:
            self.server = await asyncio.start_server(self._handle_client, host, port)
            addrs = ', '.join(str(sock.getsockname()) for sock in self.server.sockets)
            self.logger.info(f"server start listening {addrs}")
            await self.server.serve_forever()
        except Exception as e:
            self.logger.critical(f"server fails to start: {e}", exc_info=True)
            sys.exit(1)


class MettlerWorker:
    """Grab weight data from the Mettler loadcell and store realtime data as a local variable."""
    def __init__(self, ip, port=1026, frequency=100):
        self.ip = ip
        self.port = port
        self.command = "SIX1\r\n"
        self.frequency = frequency
        self.is_running = False
        self.weight = np.nan

    async def run(self):
        self.is_running = True
        print(f"connecting to {self.ip}: {self.port}")
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(self.ip, self.port), 
            timeout=2.0
        )
        while self.is_running:
            writer.write(self.command.encode("ascii"))
            await writer.drain()
            response_bytes = await asyncio.wait_for(
                reader.read(1024), 
                timeout=2.0
            )
            response_str = response_bytes.decode("ascii")
            weight_data = self.parse_six1_response(response_str)
            self.weight = weight_data["gross"]

    def parse_six1_response(self, response_str):
        """
        解析 SIX1 命令的响应字符串。
        响应格式: SIX1 Sts MinW CoZ Rep Calc PosE StepE MarkE Range TM Gross NET Tare Unit
        """
        parts = response_str.strip().split()
        if len(parts) < 15 or parts[0] != 'SIX1':
            print(f"错误：收到了意外的响应格式: {response_str}")
            return None
        try:
            status_code = parts[1]
            zero_center = parts[3] == 'Z' 
            tare_mode_code = parts[10] 
            gross_str = parts[11]
            net_str = parts[12]
            tare_str = parts[13]
            unit = parts[14]

            status_map = {'S': '稳定', 'D': '动态', '+': '过载', '-': '欠载', 'I': '无效值'}
            status = status_map.get(status_code, f'未知 ({status_code})')

            tare_mode_map = {'N': '无皮重', 'P': '预设皮重', 'M': '称量皮重'}
            tare_mode = tare_mode_map.get(tare_mode_code, f'未知 ({tare_mode_code})')

            gross = float(gross_str)
            net = float(net_str)
            tare = float(tare_str)

            return {
                "status": status, "zero_center": zero_center, "tare_mode": tare_mode,
                "gross": gross, "net": net, "tare": tare, "unit": unit
            }
        except (IndexError, ValueError) as e:
            print(f"错误：解析响应时出错: {e}\n原始响应: {response_str}")
            return None
        
    def stop(self):
        self.is_running = False

class MeterCountWorker:
    def __init__(self):
        self.meter_count = np.nan # the variable
        self.is_running = False

    def run(self):
        # raise NotImplementedError
        pass

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
