import websockets
import asyncio
import sys

class MockSensor:

    def __init__(self):
        self.is_running = False
        self.host = "0.0.0.0"
        self.port = 1026
        self.message_queue = asyncio.Queue()
        self.tasks = set()
    
    async def _handle_client(self, reader, writer):
        
        addr = writer.get_extra_info('peername')
        print(f"accept connection from {addr}")

        shutdown_signal = asyncio.Future()

        async def send_loop():
            try:
                while not shutdown_signal.done():
                    message = await self.message_queue.get()
                    
                    if message == "SI":
                        print(message)
                        writer.write("S S 0.0072 kg\n".encode("utf-8"))
                        
            except ConnectionResetError:
                # 这是关键：捕获错误
                print(f"Client {addr} forcibly closed connection (Connection reset).")
            except Exception as e:
                self.logger.error(f"error when receiving from {addr}: {e}", exc_info=True)
                if not shutdown_signal.done():
                    shutdown_signal.set_result(True)

        async def recv_loop():
            try:
                while not shutdown_signal.done():
                        data = await reader.read(1024)
                        if not data:
                            # self.logger.info(f"client {addr} has disconnected")
                            if not shutdown_signal.done():
                                shutdown_signal.set_result(True)
                        message = data.decode().strip()
                        print(f"received from {addr}: {message!r}")
                        await self.message_queue.put(message)
            except ConnectionResetError:
                # 这是关键：捕获错误
                print(f"Client {addr} forcibly closed connection (Connection reset).")
            except Exception as e:
                self.logger.error(f"error when receiving from {addr}: {e}", exc_info=True)
                if not shutdown_signal.done():
                    shutdown_signal.set_result(True)

        send_task = asyncio.create_task(send_loop())
        recv_task = asyncio.create_task(recv_loop())

        self.tasks.add(send_task)
        self.tasks.add(recv_task)

        await shutdown_signal

        send_task.cancel()
        recv_task.cancel()
        self.tasks.remove(send_task)
        self.tasks.remove(recv_task)

        print(f"connection closed")
        writer.close()
        await writer.wait_closed()

    async def _shutdown(self, sig):
        print(f"receive close signal: closing")

    async def run(self):
        try:
            self.server = await asyncio.start_server(self._handle_client, self.host, self.port)
            await self.server.serve_forever()
        except Exception as e:
            print(f"server fails to start, {e}")
            sys.exit(1)

if __name__ == "__main__":
    server_app = MockSensor()
    asyncio.run(server_app.run())