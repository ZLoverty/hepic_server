from gpiozero import RotaryEncoder
import numpy as np

class MeterCountWorker:
    """This class reads the step of a rotary encoder. It takes in the pin_number on Raspberry Pi which connect to the output of the encoder, and the diameter of the wheel, and updates its internal variable self.meter_count."""
    def __init__(self, PIN_A, PIN_B, print=False):
        self.meter_count = 0 # the variable, mm
        self.encoder = RotaryEncoder(PIN_A, PIN_B, max_steps=0)
        self.print = print

    def run(self):
        # raise NotImplementedError
        # print(f"当前步数: {encoder.steps}")
        try:
            while True:
                self.encoder.wait_for_rotate()
                self.meter_count = self.encoder.steps 
                if self.print:
                    print(f"当前步数：{self.meter_count}")
        except KeyboardInterrupt:
            print("程序退出。")

if __name__ == "__main__":

    # 定义连接的 BCM 引脚编号
    PIN_A = 17 
    PIN_B = 18
    mcw = MeterCountWorker(17, 18, print=True)
    mcw.run()
            

    
