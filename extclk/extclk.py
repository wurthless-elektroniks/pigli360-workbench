'''
EXT_CLK

Much of this was initially based on RGH1.2, written before EXT_CLK became public.
Original VHDL by Octal450, based on work by 15432 and GliGli
Thanks again to Octal for releasing the EXT_CLK source.

CPU_PLL_BYPASS doesn't work reliably when glitching Waternoose boards.
While the CPU will slow down, it will often ignore our reset pulses.

EXT_CLK was created to work around this. It asserts CPU_EXT_CLK_EN to cut the
PLL unit out of the picture and substitute an external clock source, which is
still based on the CPU's core 100 MHz clock. However, this barely slows down the
CPU: on RGH1.2, POST 0xDA takes close to 7300 usec while on EXT_CLK it takes 600 usec.
As such, the timing is super precise and the RP2040 might not be able to consistently
glitch the system.

Current status: Works, but is very slow, even with forced resets to speedup the boot.

Further reading:
https://github.com/Octal450/EXT_CLK/tree/master/matrix-coolrunner-192mhz
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

# TODO: pio just a copypaste of rgh12, have to unify that somewhere.
@rp2.asm_pio(set_init=[PIO.OUT_LOW, PIO.IN_LOW])
def extclk():
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
    set(pindirs, 3)                   [2] # 10
    set(pins, 3)                      [3] # 11
    set(pindirs, 1)                       # 12

    set(x,31) [31]
    label("p")
    nop() [31]
    nop() [31]
    jmp(x_dec,"p") [31]

    set(pins, 0)                          # 16
    wrap_target()
    nop()                                 # 17
    wrap()

pio_sm = None

SYSTEM_CLOCK = 192000000


USING_GLITCH3_IMAGE = True


def _force_reset():
    CPU_RESET.init(Pin.OUT)
    CPU_RESET.value(0)
    CPU_RESET.init(Pin.IN)

def monitor_post():
    last_post = 0
    while True:
        this_post = mem32[RP2040_GPIO_IN] >> 15
        if this_post != last_post:
            print(f"{this_post:08x}")
            last_post = this_post

def init_sm(reset_assert_delay):
    global pio_sm
    pio_sm = rp2.StateMachine(0, extclk, freq = 192000000, in_base=DBG_CPU_POST_OUT7, set_base=CPU_PLL_BYPASS)

    pio_sm.active(0)
    pio_sm.restart()
    pio_sm.active(0)
    print("restarted sm")

    mem32[0x4001c004 + (14*4)] = 0b01110011
    if mem32[0x4001c004 + (14*4)] == 0b01110011:
        print("full steam ahead!!")
    else:
        raise RuntimeError("cannot set I/O drive...")
    mem32[0x4001c004 + (13*4)] = 0b01110011

    # same delay as RGH1.2
    pll_delay = int(0.4096 * 192000000) if USING_GLITCH3_IMAGE is False else int(0.01 * 192000000)

    reset_delay = reset_assert_delay

    print("using these settings")
    print(f"- pll delay {pll_delay}")
    print(f"- reset delay {reset_delay}")

    # populate FIFO - when PIO starts, it'll grab both these values immediately
    pio_sm.put(pll_delay)
    pio_sm.put(reset_delay)
    print("buffered FIFO")


def do_reset_glitch() -> int:
    while (mem32[RP2040_GPIO_IN] & POST_BITS_MASK) != POST_D5:
        pass 
    while (mem32[RP2040_GPIO_IN] & POST_BITS_MASK) != POST_D6:
        pass 

    pio_sm.active(1)
    print("0xD6 arrived - running PIO...")

    last_post = 0

    ticks_DA = 0
    while True:
        v = mem32[RP2040_GPIO_IN]
        t = ticks_us()

        this_post = (v >> 15) & 0xFF
        if this_post != last_post:
            print(f"{this_post:02x}")
            last_post = this_post
            if this_post == 0xDA:
                ticks_DA = t
                
                print("start DA timeout")
                start_tick = t
                bits = v & POST_BITS_MASK
                while (mem32[RP2040_GPIO_IN] & POST_BITS_MASK) == bits:
                    if (ticks_us() - start_tick) > 1000:
                        print("FAIL: CPU crashed at 0xDA")
                        _force_reset()
                        return 1

        if this_post == 0xDB:
            print("got candidate!!!")
            start_tick = t
            bits = v & POST_BITS_MASK
            while (mem32[RP2040_GPIO_IN] & POST_BITS_MASK) == bits:
                if (ticks_us() - start_tick) > 200000:
                    print("FAIL: CPU crashed at 0xDB")
                    _force_reset()
                    return 1

        if this_post == 0x10:
            print("FAIL: SMC timed out")
            return 0
        
        if USING_GLITCH3_IMAGE is False and this_post == 0x22:
            start_tick = t
            bits = v & POST_BITS_MASK
            while (mem32[RP2040_GPIO_IN] & POST_BITS_MASK) == bits:
                if (ticks_us() - start_tick) > 80000:
                    print("FAIL: CPU crashed at 0x22")
                    _force_reset()
                    return 1

        if this_post == 0xF2:
            print("FAIL: hash check mismatch")
            
            # when EXT_CLK not asserted this is around 250-255 usec.
            # when it is it can be anywhere between 610-650 usec.
            print(f"-> DA -> F2 = {t-ticks_DA} usec")

            _force_reset()
            return 1


def do_reset_glitch_loop():
    freq(192000000)

    # glitch values (that start CB_B) typically happen around 614.3125 microseconds
    # 29485-29498 @ 48 MHz
    # 58971-58998 @ 96 MHz
    # 117948-117999 @ 192 MHz (117950 consistent)
    # 122864 @ 200 MHz
    
    # with 1N1412 diodes
    # @ 96 MHz:
    # - longer pulses: 58893, 58904, 58907, 58914, 58915, 58922, 58929, 58942, 58946, 58949
    # - shorter pulses: 58950 onwards
    # @ 192 MHz:
    # - longer pulses: around 117900
    # - shorter pulses: 117904-117921 works with post width of 1-3 cycles but is inconsistent
    # - Octal450's source uses ~118000 cycles
    reset_trial = 117916
    
    while True:
        print(f"start trial of: {reset_trial}")

        init_sm(reset_trial)

        result = do_reset_glitch()

        if result == 2:
            init_sm(0)
            return
        # elif result != 0:
        # reset_trial += 1
