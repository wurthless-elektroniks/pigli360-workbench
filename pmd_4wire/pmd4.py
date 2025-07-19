'''
Project Muffdiver in 4 wires
As with the 8-wire version, it doesn't work as well as RGH1.2
'''
from time import sleep, ticks_us
from machine import Pin,mem32,freq
import rp2
from rp2 import PIO


RESET_DELAY            = 1292386 # <-- start at 1292386 and go from there
ENABLE_FAST_RESET_HACK = True    # <-- leave this on to speed up attempts
BRUTE_FORCE_SEARCH     = False   # <-- set to True to increment reset delay on every attempt
BRUTE_FORCE_STEP       = 1       # positive = increase, negative = decrease
# scroll down to PIO program to change pulse width

# rpi pico
PIN_POST_7 = 13
PIN_POST_1 = 12
PIN_POST_0 = 11
PIN_CPU_RESET_IN = 10
SET_PIN_BASE = 14      # 14 = PLL, 15 = reset output
PIN_SMC_P3_GPIO0 = 9   # =DBG_LED, DB1F1

PIN_MR_BLINKY = 25     # onboard green LED


RP2040_GPIO_IN = 0xD0000004

CPU_RESET_OUT     = Pin(SET_PIN_BASE+1, Pin.IN) # will switch to output later
CPU_PLL_BYPASS    = Pin(SET_PIN_BASE+0, Pin.OUT)

DBG_CPU_POST_OUT0 = Pin(PIN_POST_7, Pin.IN, Pin.PULL_UP)
DBG_CPU_POST_OUT6 = Pin(PIN_POST_1, Pin.IN, Pin.PULL_UP)
DBG_CPU_POST_OUT7 = Pin(PIN_POST_0, Pin.IN, Pin.PULL_UP)
CPU_RESET_IN      = Pin(PIN_CPU_RESET_IN, Pin.IN, Pin.PULL_UP) # to FT2P11 under southbridge

SMC_P3_GPIO0      = Pin(PIN_SMC_P3_GPIO0, Pin.OUT)

POST7_BIT_MASK = 1 << PIN_POST_7
POST1_BIT_MASK = 1 << PIN_POST_1
POST0_BIT_MASK = 1 << PIN_POST_0
POST0_AND_1_BIT_MASK = POST1_BIT_MASK | POST0_BIT_MASK
CPU_RESET_MASK = 1 << PIN_CPU_RESET_IN

# nb: RP2040 Zero uses a WS2812B - this won't work on that board
LED = Pin(PIN_MR_BLINKY, Pin.OUT)

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
    set(pindirs, 3)                  [11] # <-- PULSE WIDTH LENGTH OVER HERE (MINUS 1)
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

def init_sm(reset_assert_delay):
    global pio_sm
    pio_sm = rp2.StateMachine(0, rgh12, freq = 48000000, in_base=DBG_CPU_POST_OUT7, set_base=CPU_PLL_BYPASS)

    pio_sm.active(0)
    pio_sm.restart()
    pio_sm.active(0)

    # set CPU reset output to fast slew rate, full power
    mem32[0x4001c004 + ((SET_PIN_BASE+1)*4)] = 0b01110011
    if mem32[0x4001c004 + ((SET_PIN_BASE+1)*4)] != 0b01110011:
        raise RuntimeError("cannot set I/O drive...")

    pll_delay = 1680000
    reset_delay = reset_assert_delay

    pio_sm.put(pll_delay)
    pio_sm.put(reset_delay)


def _force_reset():
    if ENABLE_FAST_RESET_HACK is True:
        # this looks like it's fast, but micropython's interpreter
        # is slow enough for this to actually reset the CPU
        CPU_RESET_OUT.init(Pin.OUT)
        CPU_RESET_OUT.value(0)
        CPU_RESET_OUT.init(Pin.IN)

def do_reset_glitch_loop():
    freq(192000000)

    reset_trial = RESET_DELAY

    if BRUTE_FORCE_SEARCH is True:
        reset_trial -= BRUTE_FORCE_STEP # because we're adding to it below!

    while True:
        if BRUTE_FORCE_SEARCH is True:
            reset_trial += BRUTE_FORCE_STEP

        print(f"start trial of: {reset_trial}")
        init_sm(reset_trial)
        SMC_P3_GPIO0.value(0)
        LED.value(0)

        while CPU_RESET_IN.value() == 0:
            pass
        print("CPU active")

        timeout = False
        reset_time = ticks_us()
        while (mem32[RP2040_GPIO_IN] & POST7_BIT_MASK) == 0:
            if (ticks_us() - reset_time > 1000000):
                timeout = True
                break
            LED.value(DBG_CPU_POST_OUT7.value())

        if timeout is True:
            print("FAIL: CPU stuck in coma")
            _force_reset()
            continue
        print("D0")

        # wait POST bit 1 rise - takes us to 0xD2
        while (mem32[RP2040_GPIO_IN] & POST1_BIT_MASK) == 0:
            if CPU_RESET_IN.value() == 0:
                break
            LED.value(DBG_CPU_POST_OUT7.value())

        print("D2")

        # wait POST bit 1 fall - takes us to 0xD4
        while (mem32[RP2040_GPIO_IN] & POST1_BIT_MASK) != 0:
            if CPU_RESET_IN.value() == 0:
                break
            LED.value(DBG_CPU_POST_OUT7.value())
        print("D4")

        # wait POST bit 1 rise - takes us to 0xD6
        while (mem32[RP2040_GPIO_IN] & POST1_BIT_MASK) == 0:
            if CPU_RESET_IN.value() == 0:
                break
            LED.value(DBG_CPU_POST_OUT7.value())
        print("D6")

        # RGH3 applies I2C slowdown at 0xD6 then releases it after PLL slowdown,
        # so that is replicated here
        SMC_P3_GPIO0.value(1)
        pio_sm.active(1)
        pio_sm.get()
        pio_sm.active(0)
        SMC_P3_GPIO0.value(0)


        # if bit 7 still high, the hash check failed
        if (mem32[RP2040_GPIO_IN] & POST7_BIT_MASK) != 0:
            timeout = False

            reset_time = ticks_us()
            while (mem32[RP2040_GPIO_IN] & POST7_BIT_MASK) != 0:
                if (ticks_us() - reset_time > 10000):
                    timeout = True
                    break
            
            if timeout is True:
                print("FAIL: POST bit 7 still high")
                _force_reset()
                LED.value(0)
                continue

        # wait for POST bits 0/1 to rise - that indicates we got out of CB_X
        if (mem32[RP2040_GPIO_IN] & POST0_AND_1_BIT_MASK) == 0:
            timeout = False

            reset_time = ticks_us()
            while (mem32[RP2040_GPIO_IN] & POST0_AND_1_BIT_MASK) == 0:
                if (ticks_us() - reset_time > 80000):
                    timeout = True
                    break
            
            if timeout is True:
                # possible the SMC reset on us
                if CPU_RESET_IN.value() == 0:
                    print("FAIL: SMC reset on us")
                else:
                    print("FAIL: POST bits 0/1 did not rise")
                _force_reset()
                continue

        while CPU_RESET_IN.value() != 0:
            LED.value(DBG_CPU_POST_OUT7.value())
        
        # turn LED off when a reset happens
        # or else the LED will be stuck on when powering off
        # after a successful MS kernel boot
        LED.value(0)
            
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

