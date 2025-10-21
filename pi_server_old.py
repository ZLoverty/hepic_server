import asyncio
import json
import random
import sys

delay = 0.01 # 设置数据传输频率上限为 100 Hz

async def handle_client(reader, writer):
    """
    为每一个连接的客户端创建一个独立的处理器。
    这个处理器内部包含一个发送循环和一个接收循环。
    """
    addr = writer.get_extra_info('peername')
    print(f"接受来自 {addr} 的新连接")

    # 创建一个 future，用于在任一循环结束后通知另一个循环停止
    shutdown_signal = asyncio.Future()

    # 

    async def send_loop(writer):
        """周期性地生成并发送数据给这个客户端"""
        while not shutdown_signal.done():
            try:
                # --- 这是您提供的代码，经过TCP适配 ---
                extrusion_force = 2 + random.uniform(-.2, .2)
                die_temperature = 200.0 + random.uniform(-10, 10)
                hotend_temperature = 200.0 + random.uniform(-10, 10)
                die_swell = 1.4 + random.uniform(-.1, .1)
                message = {
                    "extrusion_force": extrusion_force,
                    "die_temperature": die_temperature,
                    "die_swell": die_swell,
                    "hotend_temperature": hotend_temperature
                }
          
                # 1. 序列化成 JSON 字符串，然后编码成 bytes
                data_to_send = json.dumps(message).encode("utf-8") + b'\n' # 加一个换行符作为分隔符
                
                print(f"向 {addr} 发送 -> {message}")

                # 2. 使用 writer 写入数据，不再需要地址
                writer.write(data_to_send)
                # 3. 关键：确保数据被发送出去
                await writer.drain()
                
                # 4. 等待一段时间再发送下一次，避免刷屏和CPU 100%
                await asyncio.sleep(delay)

            except Exception as e:
                print(f"向 {addr} 发送数据时出错: {e}")
                shutdown_signal.set_result(True) # 通知接收循环也停止

    async def receive_loop(reader):
        """接收来自这个客户端的数据"""
        while not shutdown_signal.done():
            try:
                data = await reader.read(1024)
                if not data:
                    print(f"客户端 {addr} 已断开连接。")
                    shutdown_signal.set_result(True) # 通知发送循环也停止
                    break
                
                message = data.decode().strip()
                print(f"从 {addr} 收到消息: {message!r}")

            except Exception as e:
                print(f"从 {addr} 接收数据时出错: {e}")
                shutdown_signal.set_result(True)

    # 并发运行发送和接收任务
    send_task = asyncio.create_task(send_loop(writer))
    receive_task = asyncio.create_task(receive_loop(reader))

    # 等待任一任务结束
    await shutdown_signal
    
    # 清理任务
    send_task.cancel()
    receive_task.cancel()
    
    print(f"关闭与 {addr} 的连接。")
    writer.close()
    await writer.wait_closed()


async def main():
    HOST, PORT = '0.0.0.0', 10001
    server = await asyncio.start_server(handle_client, HOST, PORT)
    addrs = ', '.join(str(sock.getsockname()) for sock in server.sockets)
    print(f"服务器正在监听 {addrs}")

    async with server:
        await server.serve_forever()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n服务器正在关闭...")