'''
CAboom
Reset glitches the Xbox 360 bootrom... very slowly and unreliably.
Use CAboom ECCs in ecc/ directory.

Exploiting the bootrom isn't ideal in reset glitch scenarios, but it can be done.
From a disassembly of the bootrom:

    000046f0 addi       r4,r1,0x50
    000046f4 li         r5,0x100     <-- !!!!!!!
    000046f8 or         r3,r31,r31
    000046fc bl         FUN_000056f0 <-- memcmp()
    00004700 cmpwi      cr6,r3,0x0   <-- result must be 0 for signature check to pass
    00004704 li         r3,0x1
    00004708 beq        cr6,LAB_00004710

At POST 0x1D the RSA signature check runs. We can't tamper with the signature because RSA
is designed to prevent that. However, it still ends in a normal memcmp(), which can
be reset glitched to return 0, causing the signature check to pass.

The reset glitch affects the li r5,0x100 instruction. If that instruction is glitched,
r5 is loaded with 0 instead, and doing memcmp() with a sizeof 0 causes it to always
return "buffers matched". 

At the moment this is not ideal in real world scenarios because:
- Switching to PLL bypass or external clock mode before 0x1D slows the function down too
  much for it to be useful (can take more than 2 seconds in slowdown mode, which
  effectively makes it slower than normal RGH methods)
- Switching to those modes midway through the function probably causes some jitter which makes
  the timings impossible to nail down

This script effectively succeeds on random chance alone. At the very least, it proves
that you can glitch the bootrom.

'''
from time import sleep, ticks_us
from machine import Pin,mem32,freq
import rp2
from rp2 import PIO

# POST monitoring must be done as fast as possible
RP2040_GPIO_IN = 0xD0000004
POST_BITS_MASK = 0xFF << 15


POST_18 = 0x18 << 15 # CB is being read
POST_19 = 0x19 << 15 # CB hash being computed


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
CPU_EXT_CLK_EN      = Pin(13, Pin.OUT)

REQUEST_SOFT_RESET = Pin(10, Pin.OUT)

@rp2.asm_pio(set_init=[PIO.OUT_LOW, PIO.IN_LOW])
def caboom():
    pull(noblock)                         # 0
    mov(x, osr)                           # 1
    pull(noblock)                         # 2
    mov(y, osr)                           # 3
    wait(0, pin, 0)                       # 4
    wait(1, pin, 0)                       # 5
    wait(0, pin, 0)                       # 6
    wait(1, pin, 0)                       # 9
    label("7")
    jmp(x_dec, "7")                       # 7
    set(pins, 1)                          # 8
    label("10")
    jmp(y_dec, "10")                      # 10
    set(pindirs, 3) [2]                     # 11
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

def monitor_post():
    last_post = 0
    while True:
        this_post = mem32[RP2040_GPIO_IN] >> 15
        if this_post != last_post:
            print(f"{this_post:08x}")
            last_post = this_post

def init_sm(reset_assert_delay):
    global pio_sm
    pio_sm = rp2.StateMachine(0, caboom, freq = 192000000, in_base=DBG_CPU_POST_OUT7, set_base=CPU_EXT_CLK_EN)

    pio_sm.active(0)
    pio_sm.restart()
    pio_sm.active(0)
    print("restarted sm")

    mem32[0x4001c004 + (14*4)] = 0b01110011
    if mem32[0x4001c004 + (14*4)] == 0b01110011:
        print("full steam ahead!!")
    else:
        raise RuntimeError("cannot set I/O drive...")

    # this can probably be tweaked but you also would have to tweak the reset values.
    # do NOT go past 200 ms, the memcmp() executes not too long after
    pll_delay = int(0.198 * 192000000)

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

    # wait for POST 0x19, which is where we'll start the PIO
    while (mem32[RP2040_GPIO_IN] & POST_BITS_MASK) != POST_18:
        pass
    while (mem32[RP2040_GPIO_IN] & POST_BITS_MASK) != POST_19:
        pass

    pio_sm.active(1)
    print("0x19 arrived - running PIO...")

    last_post = 0

    ticks_1D = 0
    while True:
        v = mem32[RP2040_GPIO_IN]
        t = ticks_us()

        this_post = (v >> 15) & 0xFF
        if this_post == last_post:
            continue
        
        print(f"{this_post:02x}")
        last_post = this_post
        
        if this_post == 0x1D:
            ticks_1D = t
            start_tick = t
            bits = v & POST_BITS_MASK
            # print("check timeout...")
            while (mem32[RP2040_GPIO_IN] & POST_BITS_MASK) == bits:
                if (ticks_us() - start_tick) > 240000:
                    print("FAIL: 1D timeout")
                    _force_reset()
                    return 1

        if this_post == 0x1E:
            print("got candidate!!!")
            start_tick = t
            bits = v & POST_BITS_MASK

            # while True:
                # pass

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

        if this_post == 0x96:
            print("FAIL: signature check failed")
            # this time is nowhere near accurate thanks to micropython's interpreted nature
            # but it is close to what it should be, and that's what counts
            print(f"-> 1D -> 96 = {t-ticks_1D} usec")
            _force_reset()

            return 1

def do_reset_glitch_loop():
    # this is the key to the whole thing - you have to set frequency
    # to a multiple of 12 MHz, or this shit won't work
    freq(192000000)

    # these were test results...
    # 4372841 gave 0x1E
    # 4372820 booted!!
    # 4372844 booted as well
    # 4372910 and over don't really give any results
    reset_trial = 4372910
    while True:
        print(f"start trial of: {reset_trial}")

        init_sm(reset_trial)
        result = do_reset_glitch()

        # if result == 2:
            # init_sm(0)
            # return
        # elif result != 0:
        if result != 0:
            reset_trial -= 1
        