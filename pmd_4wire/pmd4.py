'''
RGH1.2 in Micropython, now with less wires
'''
from time import sleep, ticks_us
from machine import Pin,mem32,freq
import rp2
from rp2 import PIO


RP2040_ZERO = False
if RP2040_ZERO is True:
    # RP2040 Zero, pin configuration is temporary
    # RP2040 Zero clones can run the stock RPi Pico Micropython build.
    # note that it might be unstable - testing continues
    PIN_POST_7 = 6
    PIN_POST_1 = 7
    PIN_POST_0 = 8
    PIN_CPU_RESET_IN = 5
    SET_PIN_BASE = 10
else:
    # rpi pico
    PIN_POST_7 = 13
    PIN_POST_1 = 12
    PIN_POST_0 = 11
    PIN_CPU_RESET_IN = 10
    SET_PIN_BASE = 14  # 14 = PLL, 15 = reset output


RP2040_GPIO_IN = 0xD0000004

CPU_RESET_OUT     = Pin(SET_PIN_BASE+1, Pin.IN) # will switch to output later
CPU_PLL_BYPASS    = Pin(SET_PIN_BASE+0, Pin.OUT)

DBG_CPU_POST_OUT0 = Pin(PIN_POST_7, Pin.IN, Pin.PULL_UP)
DBG_CPU_POST_OUT6 = Pin(PIN_POST_1, Pin.IN, Pin.PULL_UP)
DBG_CPU_POST_OUT7 = Pin(PIN_POST_0, Pin.IN, Pin.PULL_UP)
CPU_RESET_IN      = Pin(PIN_CPU_RESET_IN, Pin.IN, Pin.PULL_UP) # to FT2P11 under southbridge

POST7_BIT_MASK = 1 << PIN_POST_7
POST1_BIT_MASK = 1 << PIN_POST_1
POST0_BIT_MASK = 1 << PIN_POST_0
CPU_RESET_MASK = 1 << PIN_CPU_RESET_IN

# nb: RP2040 Zero uses a WS2812B - this won't work on that board
LED = Pin(25, Pin.OUT)

GPIO0 = Pin(9, Pin.OUT)

@rp2.asm_pio(set_init=[PIO.OUT_LOW, PIO.IN_LOW])
def rgh12():
    pull(noblock)                         # 0
    mov(x, osr)                           # 1
    pull(noblock)                         # 2
    mov(y, osr)                           # 3
    wait(1, pin, 0)                       # 4
    wait(0, pin, 0)                       # 5
    wait(1, pin, 0)                       # 6
    label("7")
    jmp(x_dec, "7")                       # 7
    set(pins, 1)                          # 8
    wait(0, pin, 0)                       # 9
    label("10")
    jmp(y_dec, "10")                      # 10
    set(pindirs, 3)                  [11]  # 11
    set(pins, 3)                          # 12
    set(pindirs, 1)                       # 13
    set(y, 31)                       [31] # 14
    label("15")
    set(x, 31)                       [31] # 15
    label("16")
    nop()                            [13] # 16
    jmp(x_dec, "16")                 [31] # 17
    jmp(y_dec, "15")                 [31] # 18
    set(x, 24)                       [14] # 19
    label("20")
    jmp(x_dec, "20")                 [31] # 20
    set(pins, 0)                          # 21
    push(noblock)
    wrap_target()
    nop()                                 # 22
    wrap()

pio_sm = None

# set to True for RGH1.3, False for RGH1.2
USING_GLITCH3_IMAGE = False
RAPID_RESET = False

def init_sm(reset_assert_delay):
    global pio_sm
    pio_sm = rp2.StateMachine(0, rgh12, freq = 48000000, in_base=DBG_CPU_POST_OUT7, set_base=CPU_PLL_BYPASS)

    pio_sm.active(0)
    pio_sm.restart()
    pio_sm.active(0)
    print("restarted sm")

    # change reset output drive params
    mem32[0x4001c004 + (15*4)] = 0b01110011
    if mem32[0x4001c004 + (15*4)] == 0b01110011:
        print("full steam ahead!!")
    else:
        raise RuntimeError("cannot set I/O drive...")

    pll_delay = 1680000
    reset_delay = reset_assert_delay

    print("using these settings")
    print(f"- reset delay {reset_delay}")

    # populate FIFO - when PIO starts, it'll grab both these values immediately
    pio_sm.put(pll_delay)
    pio_sm.put(reset_delay)
    print("buffered FIFO")


def _force_reset():
    CPU_RESET_OUT.init(Pin.OUT)
    CPU_RESET_OUT.value(0)
    CPU_RESET_OUT.init(Pin.IN)

def do_reset_glitch_loop():
    freq(192000000)

    reset_trial = 1292386

    while True:
        print(f"start trial of: {reset_trial}")
        init_sm(reset_trial)
        GPIO0.value(0)

        while CPU_RESET_IN.value() == 0:
            pass
        print("CPU active")

        while DBG_CPU_POST_OUT0.value() == 0:
            LED.value(DBG_CPU_POST_OUT7.value())
        print("D0")

        while DBG_CPU_POST_OUT6.value() == 0:
            LED.value(DBG_CPU_POST_OUT7.value())
        print("D2")

        while DBG_CPU_POST_OUT6.value() != 0:
            LED.value(DBG_CPU_POST_OUT7.value())
        print("D4")

        while DBG_CPU_POST_OUT6.value() == 0:
            LED.value(DBG_CPU_POST_OUT7.value())
        print("D6")

        GPIO0.value(1)
        pio_sm.active(1)
        pio_sm.get()
        GPIO0.value(0)

        # if bit 7 still high, the hash check failed
        if DBG_CPU_POST_OUT0.value() == 1:
            timeout = False

            reset_time = ticks_us()
            while DBG_CPU_POST_OUT0.value() == 1:
                if (ticks_us() - reset_time > 10000):
                    timeout = True
                    break
            
            if timeout is True:
                print("FAIL: POST bit 7 still high")
                _force_reset()
                LED.value(0)
                continue

        # wait for POST bit 1 to rise
        if DBG_CPU_POST_OUT6.value() == 0:
            timeout = False

            reset_time = ticks_us()
            while DBG_CPU_POST_OUT6.value() == 0:
                if (ticks_us() - reset_time > 80000):
                    timeout = True
                    break
            
            if timeout is True:
                print("FAIL: POST bit 1 did not rise")
                _force_reset()
                LED.value(0)
                continue

        while CPU_RESET_IN.value() != 0:
            LED.value(DBG_CPU_POST_OUT7.value())
        
        reset_time = ticks_us()
        timeout = False
        while CPU_RESET_IN.value() == 0:
            if (ticks_us() - reset_time > 2):
                print("FAIL: SMC timeout")
                timeout = True
                break
        if timeout:
            continue

        print("should be successful???")
        while CPU_RESET_IN.value() != 0:
            pass

        print("Power off")
#        pio_sm.active(0)

