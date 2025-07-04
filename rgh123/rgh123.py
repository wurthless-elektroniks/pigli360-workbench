'''
RGH 1.2.3
The method that sounds like a shitpost, but isn't.

Thanks to 15432 for original RGH3 and Octal450 for bugfix suggestions in other glitchfiles
that were ported to this one to get it working.

This is basically RGH3 but on a microcontroller for better precision.

Status: 27 MHz mode done, 10 MHz still todo.

Bugs:
- I2C can shit itself several times. SoftI2C can crap out, or HANA/SMC
  communication can have problems leading to RROD 0010. Both mean you
  have to restart the script and/or the system after about 10 attempts.
'''

from time import sleep, ticks_us
from machine import Pin,mem32
import rp2
from rp2 import PIO

from machine import SoftI2C,freq

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


@rp2.asm_pio(set_init=[PIO.IN_LOW])
def resetter():
    pull(noblock)                         # 2
    mov(y, osr)                           # 3
    wait(1, pin, 0)                       # 0xD7
    wait(0, pin, 0)                       # 0xD8
    wait(1, pin, 0)                       # 0xD9
    wait(0, pin, 0)                       # 0xDA
    label("9")
    jmp(y_dec, "9")                       # 9
    set(pindirs, 1)                  [11] # 10
    set(pins, 1)                          # 11
    set(pindirs, 0)                       # 12
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
    push(noblock)
    wrap_target()
    nop()                                 # 15
    wrap()

pio_sm = None

def monitor_post():
    last_post = 0
    while True:
        this_post = mem32[RP2040_GPIO_IN] >> 15
        if this_post != last_post:
            print(f"{this_post:08x}")
            last_post = this_post

GLITCH_CLOCK_RATE = 48000000

def init_sm(reset_assert_delay):
    global pio_sm
    pio_sm = rp2.StateMachine(0, resetter, freq = GLITCH_CLOCK_RATE, in_base=DBG_CPU_POST_OUT7, set_base=CPU_RESET)

    pio_sm.active(0)
    pio_sm.restart()
    pio_sm.active(0)
    print("restarted sm")

    # set /RESET to: input enable, full power drive, pullup/pulldown disabled, schmitt triggered, fast slew rate
    # it's unbelievable that micropython sets the slew rate to slow by default...
    mem32[0x4001c004 + (14*4)] = 0b01110011
    if mem32[0x4001c004 + (14*4)] == 0b01110011:
        print("full steam ahead!!")
    else:
        raise RuntimeError("cannot set I/O drive...")

    # reset delay is around 26.924 ms in RGH3
    # that's about 1292352 cycles @ 48 MHz
    # actual working or plausible delay value not found yet
    reset_delay = reset_assert_delay

    print("using these settings")
    print(f"- reset delay {reset_delay}")

    # populate FIFO - when PIO starts, it'll grab both these values immediately
    pio_sm.put(reset_delay)
    print("buffered FIFO")

def _force_reset():
    CPU_RESET.init(Pin.OUT)
    CPU_RESET.value(0)
    CPU_RESET.init(Pin.IN)

def do_reset_glitch() -> int:
    CPU_PLL_BYPASS.value(0)
    i2c = SoftI2C(sda=Pin(8),scl=Pin(9),freq=100000)
    while (mem32[RP2040_GPIO_IN] & POST_BITS_MASK) != POST_D5:
        pass 
    while (mem32[RP2040_GPIO_IN] & POST_BITS_MASK) != POST_D6:
        pass

    pio_sm.active(1)

    CPU_PLL_BYPASS.value(1)
    i2c.writeto_mem(0x70, 0xCE, bytearray([0x04, 0x14, 0x44, 0xE8, 0x28]))
    CPU_PLL_BYPASS.value(0)

    print("0xD6 arrived")

    # default clock is [ 0x14, 0x44, 0xE8, 0x08 ]
    # 27 MHz mode is [ 0x14, 0x44, 0xE8, 0x28 ]
    # 15432 also found [ 0x14, 0x44, 0xE8, 0x88 ], maybe this is RGH3's 10 MHz mode?
    
    last_post = 0

    ticks_DA = 0
    ticks_after_DA = ()
    time_pio_finished = 0

    try:
        while True:
            v = mem32[RP2040_GPIO_IN]
            t = ticks_us()
            this_post = (v >> 15) & 0xFF

            # if last_post != 0xD6 and this_post == 0xD6:

            if last_post != 0xD9 and this_post == 0xD9:
                # 0.035 for 27 MHz, 0.202-0.206 for 10 MHz
                sleep(0.035)
                CPU_PLL_BYPASS.value(1)

            if last_post != 0xDA and this_post == 0xDA:
                ticks_DA = t
                pio_sm.get()
                time_pio_finished = ticks_us()

                CPU_PLL_BYPASS.value(0)
                i2c.writeto_mem(0x70, 0xCE, bytearray([0x04, 0x14, 0x44, 0xE8, 0x08]))
            
            if this_post != last_post:
                print(f"{this_post:02x}")
                last_post = this_post
                if this_post == 0xDB:
                    print("got candidate!!!")

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
                #
                # note also that if your reset wait value is too high, it will
                # cause this result to be skewed upwards.
                print(f"-> DA -> F2 = {t-ticks_DA} usec. pio {t-time_pio_finished} usec")
                return 1
    finally:
        i2c.writeto_mem(0x70, 0xCE, bytearray([0x04, 0x14, 0x44, 0xE8, 0x08]))
        CPU_PLL_BYPASS.value(0)

def do_reset_glitch_loop():
    freq(192000000)

    
    # timings assume glitch3 image. see ecc/ directory for files
    #
    # 27 MHz timings, 1N400x on bits 7-1, 1N4148 on bit 0:
    # - 1292386-1292395, though it's much wider than that obvs
    # - RGH3 prefers a wider pulse width, 8 cycles works
    #
    # 10 MHz timings:
    # - 0xDA -> 0xF2 transition at approx 150-155 ms
    #
    reset_trial = 1292388
    while True:
        print(f"start trial of: {reset_trial}")

        init_sm(reset_trial)

        result = do_reset_glitch()

        if result == 2:
            init_sm(0)
            return
        # elif result != 0:
        #reset_trial -= 1
