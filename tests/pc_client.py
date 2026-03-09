import asyncio
import json


class TCPClient:
    def __init__(self, host: str, port: int):
        self.host = host
        self.port = port
        self.reader: asyncio.StreamReader | None = None
        self.writer: asyncio.StreamWriter | None = None
        self.is_running = False
        self._receive_task: asyncio.Task | None = None
        self.messages: asyncio.Queue[dict] = asyncio.Queue()

    async def connect(self) -> bool:
        try:
            print(f"Connecting to {self.host}:{self.port} ...")
            self.reader, self.writer = await asyncio.open_connection(self.host, self.port)
            self.is_running = True
            print("Connected. Starting receive loop.")
            self._receive_task = asyncio.create_task(self.receive_data())
            return True
        except OSError as e:
            print(f"Connect failed: {e}")
            return False

    async def receive_data(self):
        print("Receive loop started.")
        while self.is_running:
            try:
                if self.reader is None:
                    break
                data = await self.reader.readline()
                if not data:
                    print("Server closed connection.")
                    self.is_running = False
                    break

                message_str = data.decode("utf-8").strip()
                if not message_str:
                    continue

                try:
                    message = json.loads(message_str)
                except json.JSONDecodeError:
                    print(f"Invalid JSON from server: {message_str}")
                    continue

                if not isinstance(message, dict):
                    print(f"Unexpected message type: {message}")
                    continue

                message_type = message.get("message_type")
                payload = message.get("payload")
                self.messages.put_nowait(message)

                if message_type == "sensor_data":
                    print(f"[sensor_data] {payload}")
                elif message_type == "sensor_config":
                    print(f"[sensor_config] {json.dumps(payload, ensure_ascii=False)}")
                else:
                    print(f"[unknown:{message_type}] {payload}")
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"Receive error: {e}")
                self.is_running = False
                break
        print("Receive loop stopped.")

    async def send_json(self, message: dict):
        if not self.is_running or not self.writer:
            print("Connection not established.")
            return
        data = json.dumps(message, ensure_ascii=False).encode("utf-8") + b"\n"
        self.writer.write(data)
        await self.writer.drain()

    async def request_sensor_config(self):
        await self.send_json({"message_type": "get_sensor_config"})
        print("Requested sensor config.")

    async def wait_for_message_type(self, message_type: str, timeout: float = 3.0) -> dict:
        while True:
            message = await asyncio.wait_for(self.messages.get(), timeout=timeout)
            if message.get("message_type") == message_type:
                return message

    async def close(self):
        if not self.is_running:
            return

        print("Closing connection...")
        self.is_running = False
        if self._receive_task:
            self._receive_task.cancel()
            await asyncio.gather(self._receive_task, return_exceptions=True)

        if self.writer:
            self.writer.close()
            await self.writer.wait_closed()
        print("Connection closed.")


async def main():
    client = TCPClient("192.168.22.65", 10001)
    try:
        print("Step 1/4: Connect to server")
        if not await client.connect():
            print("Test failed: unable to connect.")
            return

        print("Step 2/4: Request sensor config")
        await client.request_sensor_config()

        print("Step 3/4: Wait for sensor_config response")
        config_message = await client.wait_for_message_type("sensor_config", timeout=5.0)
        config_payload = config_message.get("payload")
        print(f"Received sensor_config with keys: {list(config_payload.keys()) if isinstance(config_payload, dict) else type(config_payload)}")

        print("Step 4/4: Receive sensor_data stream")
        for idx in range(3):
            data_message = await client.wait_for_message_type("sensor_data", timeout=5.0)
            print(f"sensor_data #{idx + 1}: {data_message.get('payload')}")

        print("Test passed: request config + receive data both succeeded.")
    except asyncio.TimeoutError:
        print("Test failed: timed out while waiting for expected server message.")
    finally:
        await client.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Interrupted.")
