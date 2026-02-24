from gpiozero import RotaryEncoder
import logging
import sys

class MeterCountWorker:
    """This class reads the step of a rotary encoder. It takes in the pin_number on Raspberry Pi which connect to the output of the encoder, and the diameter of the wheel, and updates its internal variable self.meter_count."""
    def __init__(self, PIN_A, PIN_B, print=False, logger=None):
        self.meter_count = 0 # the variable, mm
        self.encoder = RotaryEncoder(PIN_A, PIN_B, max_steps=0)
        self.print = print

        self.logger = logger or logging.getLogger(__name__)

    def run(self):
        try:
            while True:
                self.encoder.wait_for_rotate()
                self.meter_count = self.encoder.steps 
                if self.print:
                    self.logger.debug(f"当前步数：{self.meter_count}")
        except KeyboardInterrupt:
            self.logger.info("程序退出。")
        

if __name__ == "__main__":

    # 定义连接的 BCM 引脚编号
    # configure basic logging
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s - %(levelname)s - %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)] # 确保输出到 stdout
    )
    PIN_A = 17 
    PIN_B = 18
    mcw = MeterCountWorker(17, 18, print=True)
    mcw.run()
            

    
