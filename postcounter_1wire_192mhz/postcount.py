from time import sleep, ticks_us
from machine import Pin,mem32,freq
import rp2
from rp2 import PIO



CPU_RESET_3V3 = Pin(6, Pin.IN)

DBG_CPU_POST_OUT0 = Pin(22, Pin.IN, Pin.PULL_UP)
DBG_CPU_POST_OUT1 = Pin(21, Pin.IN, Pin.PULL_UP)
DBG_CPU_POST_OUT2 = Pin(20, Pin.IN, Pin.PULL_UP)
DBG_CPU_POST_OUT3 = Pin(19, Pin.IN, Pin.PULL_UP)
DBG_CPU_POST_OUT4 = Pin(18, Pin.IN, Pin.PULL_UP)
DBG_CPU_POST_OUT5 = Pin(17, Pin.IN, Pin.PULL_UP)
DBG_CPU_POST_OUT6 = Pin(16, Pin.IN, Pin.PULL_UP)
DBG_CPU_POST_OUT7 = Pin(15, Pin.IN, Pin.PULL_UP)

@rp2.asm_pio()
def postcount():
    wrap_target()
    wait(1, pin, 0)                       # 0
    push(noblock)                         # 1
    wait(0, pin, 0)                       # 2
    push(noblock)                         # 3
    wrap()


def count_posts():
    freq(192000000)

    # set as input
    # drive strength max
    # pull up enable
    # schmitt trigger
    # fast slew rate
    mem32[0x4001c004 + (16*4)] = 0b01111011

    pio_sm = rp2.StateMachine(0, postcount, freq = 48000000, in_base=DBG_CPU_POST_OUT7)
    pio_sm.active(1)

    # bit 0 = 28 to 0xF2
    # bit 1 = 19 to 0xF2

    post_count = 0
    while True:
        pio_sm.get()
        if CPU_RESET_3V3.value() != 0 and \
           DBG_CPU_POST_OUT0.value() != 0:
            post_count += 1
            print(post_count)
            print(f"{mem32[0xD0000004] >> 15:04x}")
        else:
            post_count = 0
