import asyncio
import json
import random
import logging
import logging.handlers
import signal
import sys
from pathlib import Path
import snap7
from snap7.util import get_real
import argparse

class PiServer:
    """
    一个健壮的、可作为服务运行的异步TCP服务器。
    它从配置文件加载设置，使用专业的日志系统，并能优雅地处理关闭信号。
    """
    def __init__(self, config_path, test_mode=False):
        self.config = self._load_config(config_path)
        self.logger = self._setup_logging()
        self.server = None
        self.tasks = set()
        self.test_mode = test_mode # if test_mode is True, generate random numbers instead of read data from PLC
        if not self.test_mode:
            # read plc related params
            self.plc_ip = self.config.get("plc_ip")
            self.weight_db = self.config.get("weight_db")
            self.weight_start = self.config.get("weight_start")
            self.meter_db = self.config.get("meter_db")
            self.meter_start = self.config.get("meter_start")
            # start plc connection
            self.plc = RobustPLC(ip_address=self.plc_ip, rack=0, slot=1)

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
        """为每个客户端连接创建独立的处理器"""
        addr = writer.get_extra_info('peername')
        self.logger.info(f"accepting new link from {addr}")

        shutdown_signal = asyncio.Future()

        async def send_loop():
            """周期性地发送数据给客户端"""
            while not shutdown_signal.done():
                try:
                    if not self.test_mode:
                        if self.plc.is_connected():
                            # read weight and meter from PLC
                            db_data = self.plc.db_read(self.weight_db, self.weight_start, 4)
                            weight = get_real(db_data, 0)
                            db_data = self.plc.db_read(self.meter_db, self.meter_start, 4)
                            meter = get_real(db_data, 0)
                        else:
                            print("读取数据失败，请检查日志。可能是连接刚刚断开。")
                            print("正在尝试重连 PLC")
                            await asyncio.sleep(2)
                            continue
                    else:
                        weight = 2 + random.uniform(-.2, .2)
                        meter = 2 + random.uniform(-.2, .2)

                    message = {
                        "weight": weight,
                        "meter": meter
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

        # 取消所有正在运行的客户端任务
        for task in list(self.tasks):
            task.cancel()
        
        if self.tasks:
            await asyncio.gather(*self.tasks, return_exceptions=True)
        
        loop = asyncio.get_running_loop()
        loop.stop()

    async def run(self):
        """启动服务器并监听信号"""
        loop = asyncio.get_running_loop()
        # 为 SIGINT (Ctrl+C) 和 SIGTERM (来自 systemd) 添加信号处理器
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, lambda s=sig: asyncio.create_task(self._shutdown(s)))

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


import snap7
import time
import logging
from threading import Lock, Thread, Event

# 配置日志记录器，方便调试
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

class RobustPLC:
    """
    一个健壮的 Siemens S7 PLC 连接器，具备自动重连和线程安全功能。
    """
    def __init__(self, ip_address, rack=0, slot=1, reconnect_interval=5):
        """
        初始化 PLC 连接器。
        :param ip_address: PLC 的 IP 地址
        :param rack: 机架号 (通常为 0)
        :param slot: 插槽号 (S7-1200/1500 通常为 1, S7-300/400 通常为 2)
        :param reconnect_interval: 自动重连尝试的间隔时间（秒）
        """
        self.ip_address = ip_address
        self.rack = rack
        self.slot = slot
        self.reconnect_interval = reconnect_interval

        self._client = snap7.client.Client()
        self._lock = Lock()  # 线程锁，确保在多线程环境下的操作安全
        self._is_connected = False
        
        # 用于后台重连线程
        self._reconnect_thread = None
        self._stop_event = Event()

    def is_connected(self):
        """返回当前的连接状态"""
        with self._lock:
            # 采用双重检查确保状态准确
            if self._is_connected:
                # get_connected() 是一个轻量级的网络检查
                self._is_connected = self._client.get_connected()
            return self._is_connected

    def connect(self):
        """
        连接到 PLC。如果连接失败，会返回 False 但不会抛出异常。
        """
        with self._lock:
            if self._is_connected:
                return True
            try:
                self._client.connect(self.ip_address, self.rack, self.slot)
                # 连接后检查 PDU 长度，这是一个很好的连接验证方法
                pdu_length = self._client.get_pdu_length()
                if pdu_length > 0:
                    self._is_connected = True
                    logging.info(f"connected to PLC {self.ip_address} PDU size: {pdu_length}")
                    # 连接成功后，启动后台健康检查和重连线程
                    self._start_reconnect_thread()
                    return True
                else: # 理论上 connect 成功 pdu 就会 > 0，但作为双重保障
                    self._is_connected = False
                    logging.warning(f"seem to connect PLC {self.ip_address} but PDU length is 0。")
                    return False
            except snap7.exceptions.Snap7Exception as e:
                self._is_connected = False
                logging.error(f"connect PLC {self.ip_address} failed: {e}")
                # 即使初次连接失败，也启动重连线程
                self._start_reconnect_thread()
                return False

    def disconnect(self):
        """
        断开与 PLC 的连接。
        """
        # 先停止后台重连线程
        self._stop_reconnect_thread()
        with self._lock:
            if self._is_connected:
                try:
                    self._client.disconnect()
                    logging.info(f"disconnected from PLC {self.ip_address}。")
                except snap7.exceptions.Snap7Exception as e:
                    logging.error(f"error connecting PLC: {e}")
            self._is_connected = False

    def _start_reconnect_thread(self):
        """启动后台健康检查和重连线程（如果尚未运行）"""
        if self._reconnect_thread is None or not self._reconnect_thread.is_alive():
            self._stop_event.clear()
            self._reconnect_thread = Thread(target=self._reconnect_handler, daemon=True)
            self._reconnect_thread.start()
            logging.info("background connection check started")

    def _stop_reconnect_thread(self):
        """停止后台重连线程"""
        if self._reconnect_thread and self._reconnect_thread.is_alive():
            self._stop_event.set()
            # 不需要 join，因为它是 daemon 线程，主程序退出它就退出
            # self._reconnect_thread.join() 
            logging.info("background connection check stopped")

    def _reconnect_handler(self):
        """
        后台线程任务：周期性地检查连接，如果断开则尝试重连。
        """
        while not self._stop_event.is_set():
            with self._lock:
                # 仅在应该连接但实际未连接时尝试重连
                if not self._is_connected:
                    logging.info(f"disconnected, will retry in {self.reconnect_interval} ")
                    # 在锁外等待，避免长时间持有锁
                    time.sleep(self.reconnect_interval) 
                    try:
                        # 重新尝试连接
                        self._client.connect(self.ip_address, self.rack, self.slot)
                        if self._client.get_connected():
                            self._is_connected = True
                            logging.info(f"connected! PLC: {self.ip_address}")
                    except snap7.exceptions.Snap7Exception:
                        logging.warning(f"connect fail PLC: {self.ip_address}")
                else:
                    # 如果已连接，就做一次健康检查
                    if not self._client.get_connected():
                        self._is_connected = False
                        logging.warning(f"lost connection! PLC: {self.ip_address}")
            
            # 无论连接状态如何，都等待一段时间再检查
            time.sleep(self.reconnect_interval)

    def read_db(self, db_number, start_offset, size):
        """
        一个受保护的 DB 读取方法。
        :return: 成功时返回 bytearray，失败时返回 None。
        """
        if not self.is_connected():
            logging.warning("fail to read：PLC disconnected。")
            return None
        
        with self._lock:
            try:
                data = self._client.db_read(db_number, start_offset, size)
                return data
            except snap7.exceptions.Snap7Exception as e:
                logging.error(f"read DB{db_number} fail: {e}")
                # 发生异常通常意味着连接已中断
                self._is_connected = False
                return None

    def write_db(self, db_number, start_offset, data):
        """
        一个受保护的 DB 写入方法。
        :param data: bytearray 类型的数据
        :return: 成功时返回 True，失败时返回 False。
        """
        if not self.is_connected():
            logging.warning("write fail：PLC disconnected。")
            return False
            
        with self._lock:
            try:
                self._client.db_write(db_number, start_offset, data)
                return True
            except snap7.exceptions.Snap7Exception as e:
                logging.error(f"write DB{db_number} fail: {e}")
                self._is_connected = False
                return False
            

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
