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
        self.config_file_path = Path(config_path).expanduser().resolve()
        self.config = self._load_config()
        self.logger = self._setup_logging()
        self.test_mode = bool(test_mode)
        self.server = None
        self.client_tasks: set[asyncio.Task] = set()
        self.sensors = {}
        self._sensors_initialized = False
        self.test_sensor_ids: list[str] = []
        self.sensor_name_by_id: dict[str, str] = {}
        self._is_shutting_down = False
        self.sensor_config_data = self._load_sensor_config_data()

        self.sensor_name_by_id = self._load_sensor_name_map()

        if self.test_mode:
            self.test_sensor_ids = self._load_test_sensor_ids()

    def _load_config(self) -> dict:
        config_file = self.config_file_path
        if not config_file.is_file():
            raise FileNotFoundError(f"Config file not found: {config_file}")
        try:
            with open(config_file, "r", encoding="utf-8") as f:
                config = json.load(f)
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON in config file {config_file}: {e}") from e

        if not isinstance(config, dict):
            raise ValueError(f"Config file must contain a JSON object: {config_file}")
        return config

    def _setup_logging(self) -> logging.Logger:
        level_name = str(self.config.get("log_level", "INFO")).upper()
        level = getattr(logging, level_name, None)
        if not isinstance(level, int):
            raise ValueError(f"Invalid log_level in config: {level_name}")
        formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")

        root_logger = logging.getLogger()
        root_logger.setLevel(level)
        root_logger.handlers.clear()
        root_stream_handler = logging.StreamHandler()
        root_stream_handler.setFormatter(formatter)
        root_logger.addHandler(root_stream_handler)

        logger = logging.getLogger("TCPServer")
        logger.setLevel(level)
        logger.handlers.clear()
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(formatter)
        logger.addHandler(stream_handler)
        logger.propagate = False
        return logger

    def _load_sensors(self):
        config_path = self._resolve_sensors_config_path()
        sensors = load_sensors_from_yaml(config_path)
        self.logger.info(f"Loaded {len(sensors)} sensors from {config_path}")
        return sensors

    def _load_sensor_config_data(self) -> dict:
        try:
            import yaml
        except ImportError as e:
            raise RuntimeError("PyYAML is required to load sensors_config.yaml. Install with: pip install PyYAML") from e

        config_path = self._resolve_sensors_config_path()
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        if not isinstance(cfg, dict):
            raise ValueError(f"Invalid sensors config format in {config_path}: root must be a mapping")
        self.logger.info(f"Loaded sensor config from {config_path}")
        return cfg

    def _resolve_sensors_config_path(self) -> Path:
        configured = self.config.get("sensors_config_path")
        if not configured:
            raise KeyError("Missing required config key: 'sensors_config_path'")

        configured_path = Path(configured).expanduser()
        if not configured_path.is_absolute():
            configured_path = (self.config_file_path.parent / configured_path).resolve()

        if not configured_path.is_file():
            raise FileNotFoundError(f"sensors_config.yaml not found at configured path: {configured_path}")

        return configured_path

    def _load_test_sensor_ids(self) -> list[str]:
        if self.sensor_name_by_id:
            sensor_ids = list(self.sensor_name_by_id.keys())
            self.logger.info(f"Loaded {len(sensor_ids)} test sensor ids from sensors config")
            return sensor_ids
        return ["loadcell_01", "rotary_encoder_01"]

    def _load_sensor_name_map(self) -> dict[str, str]:
        try:
            mapping: dict[str, str] = {}
            for item in self.sensor_config_data.get("sensors", []):
                if not isinstance(item, dict):
                    continue
                sensor_id = item.get("id")
                sensor_name = item.get("name") or sensor_id
                if sensor_id:
                    mapping[str(sensor_id)] = str(sensor_name)
            if mapping:
                self.logger.info(f"Loaded {len(mapping)} sensor names from sensors config")
            return mapping
        except (KeyError, FileNotFoundError):
            raise
        except Exception as e:
            logging.getLogger("TCPServer").warning(f"Failed to load sensor names from sensors config: {e}")
            return {}

    def _initialize_sensors(self):
        if self.test_mode or self._sensors_initialized:
            return
        try:
            self.sensors = self._load_sensors()
        except (KeyError, FileNotFoundError):
            raise
        except Exception as e:
            # Do not block server startup when sensor stack init fails.
            self.logger.error(f"Sensor initialization failed. Server will start with empty sensor set: {e}", exc_info=True)
            self.sensors = {}
        finally:
            self._sensors_initialized = True

    async def _poll_reachable_sensors(self) -> dict[str, float | None]:
        if not self.sensors:
            return {}

        sensor_ids = list(self.sensors.keys())
        tasks = [
            asyncio.wait_for(self.sensors[sensor_id].get_value(), timeout=self.config.get("sensor_timeout", 1.0))
            for sensor_id in sensor_ids
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        payload: dict[str, float | None] = {}
        for sensor_id, result in zip(sensor_ids, results):
            sensor_name = self.sensor_name_by_id.get(sensor_id, sensor_id)
            if isinstance(result, BaseException):
                self.logger.warning(f"Sensor {sensor_id} read failed: {result}")
                payload[sensor_name] = None
            elif result is None:
                self.logger.warning(f"Sensor {sensor_id} returned no data")
                payload[sensor_name] = None
            else:
                payload[sensor_name] = float(result)
        return payload

    def _build_message(self, message_type: str, payload: dict) -> bytes:
        return json.dumps({"message_type": message_type, "payload": payload}, ensure_ascii=False).encode("utf-8") + b"\n"

    def _is_sensor_config_request(self, raw_message: str) -> bool:
        message = raw_message.strip()
        if not message:
            return False
        if message.upper() in {"GET_SENSOR_CONFIG", "REQUEST_SENSOR_CONFIG"}:
            return True
        try:
            parsed = json.loads(message)
        except json.JSONDecodeError:
            return False
        if not isinstance(parsed, dict):
            return False
        message_type = str(parsed.get("message_type", "")).lower()
        action = str(parsed.get("action", "")).lower()
        return message_type in {"get_sensor_config", "request_sensor_config"} or action == "get_sensor_config"

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        peer_addr = writer.get_extra_info("peername")
        self.logger.info(f"Accepting connection from {peer_addr}")
        write_lock = asyncio.Lock()

        async def send_message(message_type: str, payload: dict):
            data_to_send = self._build_message(message_type, payload)
            async with write_lock:
                writer.write(data_to_send)
                await asyncio.wait_for(writer.drain(), timeout=self.config.get("drain_timeout", 5.0))

        current_task = asyncio.current_task()
        if current_task:
            self.client_tasks.add(current_task)

        async def send_loop():
            try:
                while True:
                    if self.test_mode:
                        message = {
                            self.sensor_name_by_id.get(sensor_id, sensor_id): 2 + random.uniform(-0.2, 0.2)
                            for sensor_id in self.test_sensor_ids
                        }
                    else:
                        message = await self._poll_reachable_sensors()

                    self.logger.debug(f"Sending sensor_data to {peer_addr} -> {message}")
                    await send_message("sensor_data", message)
                    await asyncio.sleep(self.config.get("send_delay", 0.01))
            except (ConnectionResetError, BrokenPipeError, TimeoutError, OSError) as e:
                self.logger.warning(f"Client {peer_addr} disconnected: {e}")
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
                    data = await reader.readline()
                    if not data:
                        self.logger.info(f"Client {peer_addr} has disconnected")
                        break
                    message = data.decode("utf-8", errors="ignore").strip()
                    self.logger.info(f"Received from {peer_addr}: {message!r}")
                    if self._is_sensor_config_request(message):
                        await send_message("sensor_config", self.sensor_config_data)
                        self.logger.info(f"Sent sensor_config to {peer_addr}")
            except (ConnectionResetError, BrokenPipeError, TimeoutError, OSError) as e:
                self.logger.warning(f"Client {peer_addr} disconnected: {e}")
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
        if self._is_shutting_down:
            return
        self._is_shutting_down = True

        sig_name = sig.name if sig else "UNKNOWN"
        self.logger.info(f"Received signal: {sig_name}. Shutting down.")
        if self.server:
            self.server.close()
            try:
                await self.server.wait_closed()
            except Exception:
                pass
        if self.client_tasks:
            self.logger.info(f"Cancelling {len(self.client_tasks)} active client tasks.")
            for task in list(self.client_tasks):
                task.cancel()
            await asyncio.gather(*self.client_tasks, return_exceptions=True)
        self.logger.info("Shutdown complete.")

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
            await self._shutdown(None)


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
