import argparse
import asyncio
import json
import logging
import random
import signal
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent))

try:
    from .sensor import load_sensors_from_yaml
except ImportError:
    from sensor import load_sensors_from_yaml


class PiServer:
    def __init__(self, config_path: str, test_mode: bool = False):
        self.config = self._load_config(config_path)
        self.logger = self._setup_logging()
        self.test_mode = bool(test_mode)
        self.server = None
        self.client_tasks: set[asyncio.Task] = set()
        self.sensors = {}
        self._sensors_initialized = False

    def _load_config(self, path: str) -> dict:
        config_file = Path(path).expanduser()
        if not config_file.is_file():
            logging.getLogger("TCPServer").error(f"Config file not found: {path}")
            sys.exit(1)
        with open(config_file, "r", encoding="utf-8") as f:
            return json.load(f)

    def _setup_logging(self) -> logging.Logger:
        level = self.config.get("log_level", "INFO").upper()
        formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")

        root_logger = logging.getLogger()
        root_logger.setLevel(level)
        root_logger.handlers.clear()
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(formatter)
        root_logger.addHandler(stream_handler)

        logger = logging.getLogger("TCPServer")
        logger.setLevel(level)
        logger.propagate = True
        return logger

    def _load_sensors(self):
        config_path = self.config.get("sensors_config")
        if config_path is None:
            config_path = Path(__file__).resolve().parent.parent / "sensors_config.yaml"
        else:
            config_path = Path(config_path).expanduser()
        sensors = load_sensors_from_yaml(config_path)
        self.logger.info(f"Loaded {len(sensors)} sensors from {config_path}")
        return sensors

    def _initialize_sensors(self):
        if self.test_mode or self._sensors_initialized:
            return
        try:
            self.sensors = self._load_sensors()
        except Exception as e:
            # Do not block server startup when sensor stack init fails.
            self.logger.error(f"Sensor initialization failed. Server will start with empty sensor set: {e}", exc_info=True)
            self.sensors = {}
        finally:
            self._sensors_initialized = True

    async def _poll_reachable_sensors(self) -> dict[str, float]:
        if not self.sensors:
            return {}

        sensor_ids = list(self.sensors.keys())
        tasks = [
            asyncio.wait_for(self.sensors[sensor_id].get_value(), timeout=self.config.get("sensor_timeout", 1.0))
            for sensor_id in sensor_ids
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        payload: dict[str, float] = {}
        for sensor_id, result in zip(sensor_ids, results):
            if isinstance(result, Exception):
                self.logger.debug(f"Sensor {sensor_id} read failed: {result}")
                continue
            if result is None:
                continue
            payload[sensor_id] = float(result)
        return payload

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        peer_addr = writer.get_extra_info("peername")
        self.logger.info(f"Accepting connection from {peer_addr}")

        current_task = asyncio.current_task()
        if current_task:
            self.client_tasks.add(current_task)

        async def send_loop():
            try:
                while True:
                    if self.test_mode:
                        message = {
                            "loadcell_01": 2 + random.uniform(-0.2, 0.2),
                            "rotary_encoder_01": 2 + random.uniform(-0.2, 0.2),
                        }
                    else:
                        message = await self._poll_reachable_sensors()

                    data_to_send = json.dumps(message, ensure_ascii=False).encode("utf-8") + b"\n"
                    self.logger.debug(f"Sending to {peer_addr} -> {message}")
                    writer.write(data_to_send)
                    await writer.drain()
                    await asyncio.sleep(self.config.get("send_delay", 0.01))
            except (ConnectionResetError, BrokenPipeError) as e:
                self.logger.warning(f"Disconnect from {peer_addr}: {e}")
                raise
            except asyncio.CancelledError:
                self.logger.info("Send loop cancelled.")
                raise
            except Exception as e:
                self.logger.error(f"Unexpected send error to {peer_addr}: {e}", exc_info=True)
                raise

        async def receive_loop():
            try:
                while True:
                    data = await reader.read(1024)
                    if not data:
                        self.logger.info(f"Client {peer_addr} has disconnected")
                        break
                    message = data.decode("utf-8", errors="ignore").strip()
                    self.logger.info(f"Received from {peer_addr}: {message!r}")
            except ConnectionResetError:
                self.logger.error(f"Client {peer_addr} forcibly closed connection.")
                raise
            except asyncio.CancelledError:
                self.logger.info("Receive loop cancelled.")
                raise
            except Exception as e:
                self.logger.error(f"Error receiving from {peer_addr}: {e}", exc_info=True)
                raise

        send_task = None
        receive_task = None
        try:
            send_task = asyncio.create_task(send_loop())
            receive_task = asyncio.create_task(receive_loop())
            await asyncio.wait([send_task, receive_task], return_when=asyncio.FIRST_COMPLETED)
        except asyncio.CancelledError:
            self.logger.info(f"Connection handler for {peer_addr} cancelled.")
        except Exception as e:
            self.logger.error(f"Handler exception: {e}")
        finally:
            for task in [send_task, receive_task]:
                if task and not task.done():
                    task.cancel()
            if send_task or receive_task:
                await asyncio.gather(send_task, receive_task, return_exceptions=True)

            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

            if current_task:
                self.client_tasks.discard(current_task)

    async def _shutdown(self, sig: signal.Signals):
        self.logger.info(f"Received signal: {sig.name}. Shutting down.")
        if self.server:
            self.server.close()
        if self.client_tasks:
            self.logger.info(f"Cancelling {len(self.client_tasks)} active client tasks.")
            for task in list(self.client_tasks):
                task.cancel()
            await asyncio.gather(*self.client_tasks, return_exceptions=True)
        self.logger.info("Shutdown complete.")
        asyncio.get_running_loop().stop()

    async def run(self):
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, lambda s=sig: asyncio.create_task(self._shutdown(s)))
            except NotImplementedError:
                # Windows ProactorEventLoop does not support add_signal_handler.
                self.logger.debug("Signal handlers are not supported on this platform/event loop.")
                break

        self._initialize_sensors()

        host = self.config.get("host", "0.0.0.0")
        port = self.config.get("port", 10001)

        try:
            self.server = await asyncio.start_server(self._handle_client, host, port)
            addrs = ", ".join(str(sock.getsockname()) for sock in self.server.sockets)
            self.logger.info(f"Server listening on {addrs}")
            await self.server.serve_forever()
        except asyncio.CancelledError:
            pass
        except Exception as e:
            self.logger.error(f"Server exception: {e}", exc_info=True)
        finally:
            if self.server:
                self.server.close()


def main():
    from importlib.metadata import PackageNotFoundError, version

    package_name = "hepic_server"
    try:
        app_version = version(package_name)
    except PackageNotFoundError:
        app_version = "dev-local"

    parser = argparse.ArgumentParser(description="Pi data server TCP")
    parser.add_argument("config_file", type=str, help="Path to config json file.")
    parser.add_argument(
        "-t",
        "--test",
        action="store_true",
        help="Enable test mode: generate random sensor values.",
    )
    parser.add_argument("-v", "--version", action="version", version=f"hepic_server version {app_version}")
    args = parser.parse_args()

    server_app = PiServer(args.config_file, test_mode=args.test)
    try:
        asyncio.run(server_app.run())
    except (KeyboardInterrupt, SystemExit):
        server_app.logger.info("Program closed")


if __name__ == "__main__":
    main()
