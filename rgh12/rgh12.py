'''
RGH 1.2 in Micropython, mostly for the meme
Also RGH1.3 if you set USING_GLITCH3_IMAGE to True
Runs on Raspberry Pi Pico / RP2040
Do not seriously use this unless you want to be laughed at

Original RGH1.2 V2 code by Octal450, based on work by 15432

CB POST codes will not be output on a stock console; you must write a XeLL .ecc
to see them at all.

Status:
- Falcon: Works, usually instaboots, can take 4-6 tries
- Jasper: Not tested yet
- Xenon/Zephyr: Not supported for the following reasons

About RGH1.2 on Waternoose boards:
RGH1.2 has not been made to work with Waternoose-based CPUs because of instability.
Just for fun, I looked into this with a Xenon to figure out how unstable it really was.
- The common Falcon/Jasper timings don't work; you have to search for new ones.
  Based on timings generated by the code below, the 0xDA -> 0xF2 transition happens
  around 7300 usec (same as Falcon) but in some cases can be as early as 6100-6600 usec.

- The code appears to ignore our reset pulses every time. Even if you give it a large
  reset pulse, or time the reset as early as possible (to attempt to find the 0xDA -> 0xF2
  transition point), code execution falls through to the 0xF2 error every time instead of
  resetting ahead of the error as intended.

Further reading:
https://github.com/Octal450/RGH1.2-V2-Phat/tree/master/matrix-coolrunner
'''
from time import sleep, ticks_us
from machine import Pin,mem32,freq
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
    wait(1, pin, 0)                       # 4
    wait(0, pin, 0)                       # 5
    wait(1, pin, 0)                       # 6
    label("7")
    jmp(x_dec, "7")                       # 7
    set(pins, 1)                          # 8
    wait(0, pin, 0)                       # 9
    label("10")
    jmp(y_dec, "10")                      # 10
    set(pindirs, 3)                  [3]  # 11
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
    wrap_target()
    nop()                                 # 22
    wrap()


pio_sm = None

# set to True for RGH1.3, False for RGH1.2
USING_GLITCH3_IMAGE = True

RAPID_RESET = False

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

    mem32[0x4001c004 + (14*4)] = 0b01110011
    if mem32[0x4001c004 + (14*4)] == 0b01110011:
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
    CPU_RESET.init(Pin.OUT, value = 0)
    sleep(0.001)
    CPU_RESET.init(Pin.IN)

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
        if this_post == last_post:
            continue
        
        print(f"{this_post:02x}")
        last_post = this_post
        
        if this_post == 0xDA:
            ticks_DA = t
            while mem32[RP2040_GPIO_IN] == v:
                pass
            ticks_after_DA = ticks_us()

        if this_post == 0xDB:
            print("got candidate!!!")
            start_tick = t
            bits = v & POST_BITS_MASK
            while (mem32[RP2040_GPIO_IN] & POST_BITS_MASK) == bits:
                if (ticks_us() - start_tick) > 80000:
                    print("FAIL: 0xDB timeout")
                    _force_reset()
                    return

        if USING_GLITCH3_IMAGE is True:
            if this_post == 0x54:
                # CB_X will always die at POST 0x54 upon a failed boot attempt.
                # this makes it far easier to try again in case of a failed boot
                start_tick = t
                bits = v & POST_BITS_MASK
                while (mem32[RP2040_GPIO_IN] & POST_BITS_MASK) == bits:
                    if (ticks_us() - start_tick) > 80000:
                        print("FAIL: CB_X timeout")
                        _force_reset()
                        break

        if this_post == 0x00:
            print("FAIL: SMC timed out")
            return 0

        if this_post == 0xF2:
            print("FAIL: hash check mismatch")
            
            # this time is nowhere near accurate thanks to micropython's interpreted nature
            # but it is close to what it should be, and that's what counts
            print(f"-> DA -> F2 = {t-ticks_DA} usec")
            if RAPID_RESET is True:
                _force_reset()

            return 1

def do_reset_glitch_loop():
    # this is the key to the whole thing - you have to set frequency
    # to a multiple of 12 MHz, or this shit won't work
    freq(192000000)

    # 349821 is timing file 21 and works... ehh... not great
    # 349819 boots in a couple of attempts
    # 349818 and a reset pulse width of 4 cycles instaboots my test falcon almost every time
    reset_trial = 349818

    while True:
        print(f"start trial of: {reset_trial}")

        init_sm(reset_trial)

        result = do_reset_glitch()

        # if result == 2:
            # init_sm(0)
            # return
        # elif result != 0:
        # reset_trial += 1
