'''
Project Muffdiver (PMD)

This is the Project Muffin/Mufas approach to RGH1.2.3. The SMC image must be
hacked to write to the 0xCE registers; pulling DBG_LED low enables the slowdown.

PMD's efficacy is... not great. It may just be due to bugs in this implementation, it might not.
RGH1.2.3 might be a better method simply because the Pico has more control over I2C than the SMC does.
PMD also doesn't fix the usual Jasper problems, and it is compatible with less boards than RGH1.2.

Use glitch3_smc+pmd_falcon.ecc in ECC directory.
Full Glitch3 project to be released eventually (SMC hacking is a pain in the ass).
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
DBG_CPU_POST_OUT2 = Pin(20, Pin.IN, Pin.PULL_UP) # bit 5
DBG_CPU_POST_OUT3 = Pin(19, Pin.IN, Pin.PULL_UP) # bit 4 
DBG_CPU_POST_OUT4 = Pin(18, Pin.IN, Pin.PULL_UP) # bit 3
DBG_CPU_POST_OUT5 = Pin(17, Pin.IN, Pin.PULL_UP) # bit 2
DBG_CPU_POST_OUT6 = Pin(16, Pin.IN, Pin.PULL_UP) # bit 1
DBG_CPU_POST_OUT7 = Pin(15, Pin.IN, Pin.PULL_UP) # bit 0

# outputs
CPU_RESET           = Pin(14, Pin.IN) # will switch to output later
CPU_PLL_BYPASS      = Pin(13, Pin.OUT)

DBG_LED             = Pin(10, Pin.OUT)

@rp2.asm_pio()
def transitiongetter():
    set(y, 0)                             # 0
    wait(1, pin, 0)                       # 1
    wait(0, pin, 0)                       # 2
    wait(1, pin, 0)                       # 3
    wait(0, pin, 0)                       # 4
    jmp(y_dec, "6")                       # 5
    label("6")
    jmp(pin, "8")                         # 6
    jmp(y_dec, "6")                       # 7
    label("8")
    mov(isr, y)                           # 8
    push(noblock)                         # 9
    wrap_target()
    nop()                                 # 10
    wrap()


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

USING_10_MHZ_MODE = False

pio_sm = None

def monitor_post():
    last_post = 0
    while True:
        this_post = mem32[RP2040_GPIO_IN] >> 15
        if this_post != last_post:
            print(f"{this_post:08x}")
            last_post = this_post

GLITCH_CLOCK_RATE = 48000000

def init_sm_transitiongetter():
    global pio_sm
    pio_sm = rp2.StateMachine(0, transitiongetter, freq = GLITCH_CLOCK_RATE * 2, in_base=DBG_CPU_POST_OUT7, jmp_pin=DBG_CPU_POST_OUT2)

    pio_sm.active(0)
    pio_sm.restart()
    pio_sm.active(0)
    print("transitiongetter sm armed")

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

def _i2c_go_slow():
    DBG_LED.value(1)
    
def _i2c_go_normal():
    DBG_LED.value(0)

def do_reset_glitch() -> int:
    CPU_PLL_BYPASS.value(0)
    _i2c_go_normal()

    while (mem32[RP2040_GPIO_IN] & POST_BITS_MASK) != POST_D5:
        pass
    while (mem32[RP2040_GPIO_IN] & POST_BITS_MASK) != POST_D6:
        pass

    pio_sm.active(1)

    # per 15432:
    # it's better to force PLL bypass mode because, once the I2C slowdown kicks in,
    # it gives time for the PLL to lock to the slower frequency

    i2c_slowed = True
    _i2c_go_slow()

    print("0xD6 arrived")

    last_post = 0

    ticks_DA = 0
    ticks_after_DA = ()
    time_pio_finished = 0

    try:
        while True:
            v = mem32[RP2040_GPIO_IN]
            t = ticks_us()
            this_post = (v >> 15) & 0xFF

            if last_post != 0xD9 and this_post == 0xD9:
                # 0.035 for 27 MHz, 0.096 for 10 MHz
                sleep(0.096 if USING_10_MHZ_MODE else 0.035)
                CPU_PLL_BYPASS.value(1)

            if last_post != 0xDA and this_post == 0xDA:
                ticks_DA = t

                # for transitiongetter debug
                # print(~pio_sm.get() + 0x0100000000)
                
                pio_sm.get()
                time_pio_finished = ticks_us()

                CPU_PLL_BYPASS.value(0)
                _i2c_go_normal()
                i2c_slowed = False
            
            if this_post != last_post:
                print(f"{this_post:02x}")
                last_post = this_post
                if this_post == 0xDB:
                    print("got candidate!!!")
                    # return 2

            if this_post == 0x54:
                # CB_X will always die at POST 0x54 upon a failed boot attempt.
                # this makes it far easier to try again in case of a failed boot
                start_tick = t
                bits = v & POST_BITS_MASK
                while (mem32[RP2040_GPIO_IN] & POST_BITS_MASK) == bits:
                    if (ticks_us() - start_tick) > 80000:
                        print("FAIL: CB_X timeout")
                        _force_reset()
                        return

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
                _force_reset()
                return 1
    finally:
        if i2c_slowed is True:
            _i2c_go_normal()
        CPU_PLL_BYPASS.value(0)

def do_reset_glitch_loop():
    freq(192000000)
    
    # timings assume glitch3 image. see ecc/ directory for files
    #
    # 27 MHz timings, 1N400x on bits 7-1, 1N4148 on bit 0:
    # - 0xDA -> 0xF2 happens around 1324079 cycles
    # - Winning values: 1292386-1292395, though it's much wider than that obvs
    #
    # 10 MHz timings:
    # - 0xDA -> 0xF2 transition at approx 76000 usec
    #   (3575010-3575012 cycles @ 48 MHz, timing varies)
    # - Winning values: 3489416-3489438 (3489416 earliest)
    # - 10 MHz mode seems to play nicer with a short pulse width; long pulse widths
    #   can cause XeLL to freeze at boot or throw an exception later during init.
    #
    reset_trial = 3489424 if USING_10_MHZ_MODE is True else 1292386
    while True:
        print(f"start trial of: {reset_trial}")

        init_sm(reset_trial)

        result = do_reset_glitch()

        if result == 2:
            init_sm(0)
            return
        # elif result == 1:
        # reset_trial += 1
