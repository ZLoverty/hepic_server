import asyncio
import json

class TCPClient:
    def __init__(self, host, port):
        self.host = host
        self.port = port
        self.reader = None
        self.writer = None
        self.is_running = False
        self._receive_task = None 

    async def connect(self):
        """建立连接，并启动后台接收任务"""
        try:
            print(f"正在连接到服务器 {self.host}:{self.port}...")
            self.reader, self.writer = await asyncio.open_connection(self.host, self.port)
            self.is_running = True
            print("连接成功！服务器应该会马上开始发送数据。")
            
            # 启动一个后台任务专门用于接收数据
            self._receive_task = asyncio.create_task(self.receive_data())
            return True
            
        except OSError as e:
            print(f"连接失败: {e}")
            return False

    async def receive_data(self):
        """
        持续接收数据。这是客户端的核心。
        """
        print("数据接收循环已启动...")
        while self.is_running:
            try:
                # 1. 使用 readline() 读取一行数据，直到遇到换行符 \n
                #    这是最高效的方式，因为它精确地匹配了服务器的发送格式。
                data = await self.reader.readline()
                
                if not data:
                    print("连接被服务器关闭。")
                    self.is_running = False
                    break

                # 2. 解码并去除可能存在的空白符
                message_str = data.decode('utf-8').strip()
                if not message_str:
                    continue

                # 3. 解析JSON字符串为Python字典
                try:
                    message_dict = json.loads(message_str)
                    print(f"收到 -> {message_dict}")
                    # 在GUI应用中，你可以在这里发射信号:
                    # self.data_received.emit(message_dict)
                except json.JSONDecodeError:
                    print(f"错误：收到无法解析的JSON数据: {message_str}")

            except asyncio.CancelledError:
                # 当 close() 方法被调用时，我们会进入这里
                break
            except Exception as e:
                print(f"接收数据时出错: {e}")
                self.is_running = False
        
        print("数据接收循环已停止。")

    async def send_data(self, message):
        """向服务器发送一条消息"""
        if not self.is_running or not self.writer:
            print("连接未建立，无法发送消息。")
            return

        try:
            # 客户端发送时也最好加上换行符，以方便服务器按行读取
            data_to_send = (message + '\n').encode('utf-8')
            self.writer.write(data_to_send)
            await self.writer.drain()
            print(f"已发送 -> {message}")
        except Exception as e:
            print(f"发送消息时出错: {e}")
            self.is_running = False

    async def close(self):
        """优雅地关闭连接"""
        if not self.is_running:
            return
            
        print("正在关闭连接...")
        self.is_running = False

        if self._receive_task:
            self._receive_task.cancel()
            # 等待任务真正被取消
            await asyncio.gather(self._receive_task, return_exceptions=True)

        if self.writer:
            self.writer.close()
            await self.writer.wait_closed()
        
        print("连接已关闭。")


async def main():
    # 确保你的服务器脚本正在运行
    client = TCPClient('127.0.0.1', 10001)
    
    try:
        if await client.connect():
            # 连接成功后，接收任务已经在后台自动运行了
            # 主程序可以在这里做其他事情，或者像这样发送几条消息
            
            await client.send_data("Hello, Server! This is the client.")
            await asyncio.sleep(1)
            await client.send_data("start")
            await asyncio.sleep(3)
            await client.send_data("stop")
            
    finally:
        # 无论程序如何退出，都确保关闭连接
        await client.close()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n程序被用户中断。")