'''
RGH 1.2 in Micropython, mostly for the meme
Runs on Raspberry Pi Pico / RP2040
Do not seriously use this unless you want to be laughed at

CB POST codes will not be output on a stock console; you must write a XeLL .ecc
to see them at all.

Further reading:
https://github.com/Octal450/RGH1.2-V2-Phat/tree/master/matrix-coolrunner
'''
from time import sleep, ticks_us
from machine import Pin,mem32
import rp2
from rp2 import PIO

# POST monitoring must be done as fast as possible
RP2040_GPIO_IN = 0xD0000004
POST_BITS_MASK = 0xFF << 15
POST_D5        = 0xD5 << 15
POST_D6        = 0xD6 << 15


# actual logical bits are reversed
# all are connected to the POST pins via diodes as in RGH3
# DBG_CPU_POST_OUT7 = post bit 0 for RGH 1.2
# DBG_CPU_POST_OUT6 = post bit 1 for RGH 1, 3
DBG_CPU_POST_OUT0 = Pin(22, Pin.IN, Pin.PULL_UP)
DBG_CPU_POST_OUT1 = Pin(21, Pin.IN, Pin.PULL_UP)
DBG_CPU_POST_OUT2 = Pin(20, Pin.IN, Pin.PULL_UP)
DBG_CPU_POST_OUT3 = Pin(19, Pin.IN, Pin.PULL_UP)
DBG_CPU_POST_OUT4 = Pin(18, Pin.IN, Pin.PULL_UP)
DBG_CPU_POST_OUT5 = Pin(17, Pin.IN, Pin.PULL_UP)
DBG_CPU_POST_OUT6 = Pin(16, Pin.IN, Pin.PULL_UP)
DBG_CPU_POST_OUT7 = Pin(15, Pin.IN, Pin.PULL_UP)

# outputs
CPU_RESET           = Pin(14, Pin.IN) # will switch to output later
CPU_PLL_BYPASS      = Pin(13, Pin.OUT)

# generate this from rgh12.pio - do NOT modify by hand
# if you need to regenerate it, use https://wokwi.com/tools/pioasm
# do NOT change set_init params unless you know what you're doing
@rp2.asm_pio(set_init=[PIO.OUT_LOW, PIO.IN_LOW])
def rgh12():
    pull(noblock)                         # 0
    mov(x, osr)                           # 1
    pull(noblock)                         # 2
    mov(y, osr)                           # 3
    wait(0, pin, 1)                       # 4
    wait(1, pin, 0)                       # 5
    label("6")
    jmp(x_dec, "6")                       # 6
    set(pins, 1)                          # 7
    wait(1, pin, 1)                       # 8
    label("9")
    jmp(y_dec, "9")                       # 9
    set(pindirs, 3)                  [1]  # 10
    set(pins, 3)                          # 11
    set(pindirs, 1)                       # 12
    set(pins, 1)                          # 13
    set(y, 31)                            # 14
    label("15")
    set(x, 31)                            # 15
    label("16")
    jmp(x_dec, "16")                 [31] # 16
    jmp(y_dec, "15")                      # 17
    set(pins, 0)                          # 18
    wrap_target()
    nop()                                 # 19
    wrap()

pio_sm = None

def monitor_post():
    last_post = 0
    while True:
        this_post = mem32[RP2040_GPIO_IN] >> 15
        if this_post != last_post:
            print(f"{this_post:08x}")
            last_post = this_post

def init_sm(reset_assert_delay):
    global pio_sm
    pio_sm = rp2.StateMachine(0, rgh12, freq = 48000000, in_base=DBG_CPU_POST_OUT7, set_base=CPU_PLL_BYPASS)

    pio_sm.active(0)
    pio_sm.restart()
    pio_sm.active(0)
    print("restarted sm")

    # drive /RESET line at full power (12 mA)
    mem32[0x4001c004 + (15*4)] = (mem32[0x4001c004 + (15*4)] & 0b11001111) | 0b00110000
    
    if ((mem32[0x4001c004 + (15*4)]) & 0b00110000) == 0b00110000:
        print("full steam ahead!!")
    else:
        raise RuntimeError("cannot set I/O drive...")

    # the "pll delay" is the amount of time we wait between POST 0xD9
    # and when CPU_PLL_BYPASS is asserted.
    #
    # this value is 9600 * 1024 * 2. the matrix/coolrunner source doesn't count
    # these manually, instead preferring to use a divider, most likely to save
    # cell space on the FPGA.
    pll_delay = 19660800

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
    #
    # I was able to get odd results around 350208 cycles - these would generate POST 0xAF
    # which is a CB error ("not enough memory").
    reset_delay = reset_assert_delay

    print("using these settings")
    print(f"- pll delay {pll_delay}")
    print(f"- reset delay {reset_delay}")

    # populate FIFO - when PIO starts, it'll grab both these values immediately
    pio_sm.put(pll_delay)
    pio_sm.put(reset_delay)
    print("buffered FIFO")


def do_reset_glitch() -> int:
    #
    # micropython isn't fast enough to keep up with all the POST toggles
    # so we have to read all the bits.
    #
    # this also gives us an advantage of knowing exactly what went wrong in the case
    # of a failed boot.
    #
    # kinda surprised commercial glitch chips never bothered to monitor the full POST bus...
    #
    while (mem32[RP2040_GPIO_IN] & POST_BITS_MASK) != POST_D5:
        pass 
    while (mem32[RP2040_GPIO_IN] & POST_BITS_MASK) != POST_D6:
        pass 

    pio_sm.active(1)
    print("0xD6 arrived - running PIO...")

    last_post = 0

    ticks_DA = 0
    ticks_after_DA = ()
    while True:
        v = mem32[RP2040_GPIO_IN]
        t = ticks_us()

        this_post = (v >> 15) & 0xFF
        if this_post != last_post:
            print(f"{this_post:02x}")
            last_post = this_post
        
        if last_post == 0xDA:
            ticks_DA = t
            while mem32[RP2040_GPIO_IN] == v:
                pass
            ticks_after_DA = ticks_us()

        if this_post == 0xDB:
            print("got candidate!!!")
            return 1

        if this_post == 0x00:
            print("FAIL: SMC timed out")
            return 0

        if this_post == 0xF2:
            print("FAIL: hash check mismatch")
            
            # this time is nowhere near accurate thanks to micropython's interpreted nature
            # but it is close to what it should be, and that's what counts
            print(f"-> DA -> F2 = {t-ticks_DA} usec")
            return 1

def do_reset_glitch_loop():
    reset_trial = 349821 # int(48 * 7296) # int(48 * 7400) # = 349821
    
    while True:
        print(f"start trial of: {reset_trial}")

        init_sm(reset_trial)

        result = do_reset_glitch()

        if result == 2:
            init_sm(0)
            return
        elif result != 0:
            reset_trial += 1
