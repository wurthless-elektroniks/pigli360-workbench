'''
RGH1.2 in Micropython, now with less wires
'''
from time import sleep, ticks_us
from machine import Pin,mem32,freq
import rp2
from rp2 import PIO

RESET_DELAY            = 349821  # <-- start at 349821
ENABLE_FAST_RESET_HACK = True    # <-- leave this on to speed up attempts
BRUTE_FORCE_SEARCH     = False   # <-- set to True to increment reset delay on every attempt
BRUTE_FORCE_STEP       = 1       # positive = increase, negative = decrease
# scroll down to PIO program to change pulse width

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
POST0_AND_1_BIT_MASK = POST1_BIT_MASK | POST0_BIT_MASK
CPU_RESET_MASK = 1 << PIN_CPU_RESET_IN

# nb: RP2040 Zero uses a WS2812B - this won't work on that board
LED = Pin(25, Pin.OUT)

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
    set(pindirs, 3)                  [3]  # <-- PULSE WIDTH LENGTH OVER HERE (MINUS 1)
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
    mem32[0x4001c004 + ((SET_PIN_BASE+1)*4)] = 0b01110011
    if mem32[0x4001c004 + ((SET_PIN_BASE+1)*4)] == 0b01110011:
        print("full steam ahead!!")
    else:
        raise RuntimeError("cannot set I/O drive...")
    

    # the "pll delay" is the amount of time we wait between POST 0xD9
    # and when CPU_PLL_BYPASS is asserted.
    #
    # for glitch2 images (standard RGH1.2):
    # this value is 9600 * 1024 * 2. the matrix/coolrunner source doesn't count
    # these manually, instead preferring to use a divider, most likely to save
    # cell space on the FPGA.
    #
    # for glitch3 images (this approach is nicknamed "RGH1.3"):
    # don't go past 408000. even at this value, you'll get failed boots.
    pll_delay = 19660800 if USING_GLITCH3_IMAGE is False else 408000

    # the "pulse delay" is how long to wait before asserting /RESET after POST 0xDA,
    # give or take a few cycles for the PIO to do stuff.
    #
    # to find the right timing for this, check the POST code when the reset happens.
    #
    # when using a large reset pulse (i.e., you're intentionally doing a full CPU reset),
    # if 0x00 is returned, the CPU rebooted too early.
    # if 0xF2 is returned, the hash check failed and the value is too high.
    #
    # the delay should be 7200-7300 microseconds, with RGH1.2 V2 timing file 21's
    # preferred value being 7287.9375 microseconds (349821 cycles).
    # if you find the 0xDA -> 0xF2 transition is nowhere near this value,
    # something is wrong.
    reset_delay = reset_assert_delay

    print("using these settings")
    print(f"- pll delay {pll_delay}")
    print(f"- reset delay {reset_delay}")

    # populate FIFO - when PIO starts, it'll grab both these values immediately
    pio_sm.put(pll_delay)
    pio_sm.put(reset_delay)
    print("buffered FIFO")


def _force_reset():
    if ENABLE_FAST_RESET_HACK is True:
        # this looks like it's fast, but micropython's interpreter
        # is slow enough for this to actually reset the CPU
        CPU_RESET_OUT.init(Pin.OUT)
        CPU_RESET_OUT.value(0)
        CPU_RESET_OUT.init(Pin.IN)

def do_reset_glitch_loop():
    # this is the key to the whole thing - you have to set frequency
    # to a multiple of 12 MHz, or this shit won't work
    freq(192000000)

    # in a 4-wire configuration:
    # 21 seems to work okay for falcon
    # 24 seems to work okay for jasper
    # this timing value will depend on your wiring, obvs
    reset_trial = RESET_DELAY

    if BRUTE_FORCE_SEARCH is True:
        reset_trial -= BRUTE_FORCE_STEP # because we're adding to it below!
    
    while True:
        if BRUTE_FORCE_SEARCH is True:
            reset_trial += BRUTE_FORCE_STEP
        print(f"start trial of: {reset_trial}")
        init_sm(reset_trial)
        LED.value(0)

        while CPU_RESET_IN.value() == 0:
            pass
        print("CPU active")

        timeout = False
        reset_time = ticks_us()
        while DBG_CPU_POST_OUT0.value() == 0:
            if (ticks_us() - reset_time > 1000000):
                timeout = True
                break
            LED.value(DBG_CPU_POST_OUT7.value())

        if timeout is True:
            print("FAIL: CPU stuck in coma")
            continue

        print("D0")

        # wait POST bit 1 rise - takes us to 0xD2
        while DBG_CPU_POST_OUT6.value() == 0:
            LED.value(DBG_CPU_POST_OUT7.value())

        print("D2")

        # wait POST bit 1 fall - takes us to 0xD4
        while DBG_CPU_POST_OUT6.value() != 0:
            LED.value(DBG_CPU_POST_OUT7.value())

        print("D4")

        # wait POST bit 1 rise - takes us to 0xD6
        while DBG_CPU_POST_OUT6.value() == 0:
            LED.value(DBG_CPU_POST_OUT7.value())
        print("D6")
        
        # run PIO and block until it finishes
        pio_sm.active(1)
        pio_sm.get()
        pio_sm.active(0)
        
        # speedup hacks only
        if ENABLE_FAST_RESET_HACK is True:
            # if bit 7 still high, the hash check failed
            if (DBG_CPU_POST_OUT0.value()) != 0:
                timeout = False

                reset_time = ticks_us()
                while (DBG_CPU_POST_OUT0.value()) != 0:
                    if (ticks_us() - reset_time > 10000):
                        timeout = True
                        break
                
                if timeout is True:
                    print("FAIL: POST bit 7 still high")
                    _force_reset()
                    LED.value(0)
                    continue

            # RGH1.3 only: wait for POST bits 0/1 to rise - that indicates we got out of CB_X.
            # if we don't see them in time, the boot has failed
            if USING_GLITCH3_IMAGE is True and (mem32[RP2040_GPIO_IN] & POST0_AND_1_BIT_MASK) == 0:
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

