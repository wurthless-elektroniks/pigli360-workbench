'''
Manual clock attack attempt for Xenon boards

In short: Disable backup clock generator via I2C and substitute our own.
Likelihod of success: slim.

Xenon boards do not have the HANA as the HANA wasn't a thing at the time.
Instead they had ANA, which does the same functions (minus HDMI), but its internal
clock generators for the CPU and GPU were not stable enough for production (another sign
the 360 was a product rushed to market).

The CPU clock generator is a CY28517ZXC PCI Express clock generator. Unsurprisingly, it
also generates virtually every important clock signal in the system. I won't cover the
48 MHz standby clock or 25 MHz ethernet/SATA clocks here because they aren't relevant.

The four 100 MHz differentially signalled clock signals are as follows:
- A: GPU
- B: CPU
- C: SATA
- D: PCIe

The chip is a SMBus device and as such is accessible over I2C on address 0x69
(that's 1101001 = 0x69, someone at Cypress was having a laff).
The default configuration is:
- Byte 0 = 0xFF (enable all clock outputs)
- Byte 1 = 0x83 (maximum drive strength, spread control enabled, spread control = -0.50 Lexmark)
- Byte 2 = 0x00 (reserved, leave zero)
- Byte 3 = 0x00 (reserved, leave zero)
- Byte 4 = 0xF8 (all reserved bits set, VCO frequency control disabled)
- Byte 5 = 0x00 (reserved, leave zero)
- Byte 6 = 0x08 (vendor = 8, revision code = 0, consistent with CY28517 datasheet)

To disable the CPU clock output simply set byte 0 to 0xFD (disables output B, controlled by bit 1).

    i2c.writeto_mem(0x69, 0, bytes([0x01, 0xfd]))

Obviously, the RP2040 can't generate a stable-enough clock for the CPU to run at full speed,
so we have to turn the clock off, substitute our own, run the glitch,
then switch the CPU's real clock back on.

Status: Not working. Might never work. The clock we feed to the CPU isn't as clean as
it would expect. A stable clock generator would have to be temperature compensated.
What successes we get from this code will essentially be random.

Bugs:
- As with RGH1.2.3, I2C can misbehave, leading to ENODEV errors and RRODs.
  If RROD 0012 or program hangs, you need to unplug and try again.
'''

from time import sleep, ticks_us
from machine import Pin,mem32,freq,SoftI2C
import rp2
from rp2 import PIO


# standard pins, whatevs
RP2040_GPIO_IN = 0xD0000004
POST_BITS_MASK = 0xFF << 15
POST_D5        = 0xD5 << 15
POST_D6        = 0xD6 << 15

CPU_RESET           = Pin(14, Pin.IN) # will switch to output later
CPU_PLL_BYPASS      = Pin(13, Pin.OUT)
DBG_CPU_POST_OUT0 = Pin(22, Pin.IN, Pin.PULL_UP)
DBG_CPU_POST_OUT1 = Pin(21, Pin.IN, Pin.PULL_UP)
DBG_CPU_POST_OUT2 = Pin(20, Pin.IN, Pin.PULL_UP)
DBG_CPU_POST_OUT3 = Pin(19, Pin.IN, Pin.PULL_UP)
DBG_CPU_POST_OUT4 = Pin(18, Pin.IN, Pin.PULL_UP)
DBG_CPU_POST_OUT5 = Pin(17, Pin.IN, Pin.PULL_UP)
DBG_CPU_POST_OUT6 = Pin(16, Pin.IN, Pin.PULL_UP)
DBG_CPU_POST_OUT7 = Pin(15, Pin.IN, Pin.PULL_UP)

# connect 33 or 47 ohm resistors in series and connect them to the test points under the PCB
CPU_CLK_DP_R = Pin(4, Pin.IN) # R3C11
CPU_CLK_DN_R = Pin(5, Pin.IN) # R3C12

# ... pins 8, 9 reserved for I2C ...

# ---------------------------

@rp2.asm_pio(set_init=[PIO.OUT_LOW, PIO.OUT_HIGH])
def clock_gen():
    wrap_target()
    set(pins, 1) # positive high, negative low
    set(pins, 0) # negative high, positive low
    wrap()

@rp2.asm_pio(set_init=[PIO.IN_LOW, PIO.IN_LOW])
def clock_gen_stubbed():
    wrap_target()
    nop()
    wrap()

@rp2.asm_pio(set_init=[PIO.OUT_LOW, PIO.IN_LOW])
def manclk_reset_only():
    pull(noblock)                         # 0
    mov(y, osr)                           # 1
    wait(1, pin, 1)                       # 2
    label("3")
    jmp(y_dec, "3")                       # 3
    set(pindirs, 3)                  [15] # 4
    set(pins, 3)                          # 5
    set(pindirs, 1)                       # 6
    # push(noblock)                         # 7
    wrap_target()
    nop()                                 # 8
    wrap()

# ---------------------------

# the actual clock rate/slowdown on the CPU isn't quite clear to me.
# note that the higher the clock divider, the more variance there will be
# in potential output timings.
MANCLK_CLOCK_DIV = 64
FAKE_CLOCK_RATE = int(200000000 / MANCLK_CLOCK_DIV)

def setup_fake_clock_gen():


    sm = rp2.StateMachine(7, clock_gen, freq = FAKE_CLOCK_RATE, set_base=CPU_CLK_DP_R)
    sm.active(1)

def kill_fake_clock_gen():
    sm = rp2.StateMachine(7, clock_gen_stubbed, freq = FAKE_CLOCK_RATE, set_base=CPU_CLK_DP_R)
    sm.active(1)

def monitor_post():
    last_post = 0
    while True:
        this_post = mem32[RP2040_GPIO_IN] >> 15
        if this_post != last_post:
            print(f"{this_post:08x}")
            last_post = this_post


pio_sm = None
i2c = SoftI2C(sda=Pin(8),scl=Pin(9),freq=100000)


def init_sm(reset_assert_delay):
    global pio_sm 
    pio_sm = rp2.StateMachine(0, manclk_reset_only, freq = 200000000, in_base=DBG_CPU_POST_OUT7, set_base=CPU_PLL_BYPASS)

    pio_sm.active(0)
    pio_sm.restart()
    pio_sm.active(0)
    print("restarted sm")

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


def do_reset_glitch() -> int:
    # enable CPU normal clock gen at the start of every cycle
    kill_fake_clock_gen()
    i2c.writeto_mem(0x69, 0, bytes([0x01, 0xFF]))

    while (mem32[RP2040_GPIO_IN] & POST_BITS_MASK) != POST_D5:
        pass
    while (mem32[RP2040_GPIO_IN] & POST_BITS_MASK) != POST_D6:
        pass

    last_post = 0
    ticks_DA = 0

    try:
        while True:
            v = mem32[RP2040_GPIO_IN] & POST_BITS_MASK
            t = ticks_us()
            this_post = (v >> 15) & 0xFF

            if this_post != last_post:
                print(f"{this_post:02x}")
                last_post = this_post
                
                if this_post == 0xD9:
                    # the closer to 0xDA you get, the less time the PLL will get to stabilize.
                    # use 0.4096 as a safe value.
                    sleep(0.4096)

                    # kill real clock generator
                    i2c.writeto_mem(0x69, 0, bytes([0x01, 0xFD]))

                    # startup fake clock generator
                    setup_fake_clock_gen()

                    # also start glitcher statemachine
                    pio_sm.active(1)

                if this_post == 0xDB:
                    print("got candidate!!!")

            if this_post == 0xDA:
                ticks_DA = t
                while (mem32[RP2040_GPIO_IN] & POST_BITS_MASK) == v:
                    t = ticks_us()
                this_post = (v >> 15) & 0xFF

            if this_post == 0x00:
                print("FAIL: SMC timed out")
                return 0
            
            if this_post == 0xF2:
                print("FAIL: hash check mismatch")
                print(f"-> DA -> F2 = {t-ticks_DA} usec")
                return 1
    finally:
        kill_fake_clock_gen()
        i2c.writeto_mem(0x69, 0, bytes([0x01, 0xFF]))

def do_reset_glitch_loop():
    freq(200000000)
    
    mem32[0x4001c004 + (4*4)] = 0b01110011
    mem32[0x4001c004 + (5*4)] = 0b01110011

    reset_trial = 800000

    while True:
        print(f"start trial of: {reset_trial}")

        init_sm(reset_trial)

        result = do_reset_glitch()

        if result == 2:
            init_sm(0)
            return
        elif result != 0:
            reset_trial += 1
