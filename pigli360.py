'''
pigli360.py
Common glitching framework because the scattered implementations were getting unmanagable
'''
 
import rp2
from machine import Pin,mem32,SoftI2C
from time import sleep, sleep_ms, ticks_us
from enum import Enum

BOARD = 'pico'

if BOARD == 'pico':
    POST_PIN_BASE_ID  = 15  # 15-22
    CPU_CTRL_BASE_ID  = 13  # 13 = PLL, 14 = reset
elif BOARD == 'rp2040zero':
    POST_PIN_BASE_ID  = 0
    CPU_CTRL_BASE_ID  = 8
else:
    raise RuntimeError(f"unsupported board: {BOARD}")

# POST monitoring must be done as fast as possible
RP2040_GPIO_IN = 0xD0000004

POST_IO_BASE = 15
POST_BITS_MASK = 0xFF << POST_PIN_BASE_ID
def _make_post(x):
    return x << POST_PIN_BASE_ID

def _unpack_post(x):
    return (x >> POST_PIN_BASE_ID) & 0xFF

# important POST codes
POST_00 = _make_post(0x00)
'''
CPU reset by SMC or system powered off
'''

POST_10 = _make_post(0x10)
'''
Indicates either that the bootrom has started executing (if POST 0x00 preceded it)
or that the XeLL payload has started.
'''

POST_11 = _make_post(0x11)
'''
'''

POST_D5 = _make_post(0xD5)
'''
`FETCH_CONTENTS_CB_B`. Copy CB_B from flash into SRAM. setup for 0xD6
'''

POST_D6 = _make_post(0xD6)
'''
`HMACSHA_COMPUTE_CB_B`. Create HMAC key to decrypt CD later on.
CB_A won't touch flash past this point, so this makes it
an ideal point to start our workflows
'''

POST_D9 = _make_post(0xD9)
'''
`SHA_COMPUTE_CB_B`. Compute SHA hash of CB_B. Typically where slowdown is applied for RGH1.2.
'''

POST_DA = _make_post(0xDA)
'''
`SHA_VERIFY_CB_B`. Executing CB_B signature memcmp()
'''

POST_DB        = 0xDB << POST_IO_BASE
'''
`BRANCH_CB_B`. Signature check passed, jumping to CB_B.
'''

POST_20 = _make_post(0x20)
POST_21 = _make_post(0x21)
POST_22 = _make_post(0x22)

POST_FB = _make_post(0xFB)
'''
CB_B hash check failed, CPU halted.
'''

# actual logical bits are reversed
# all must be connected to the POST pins via diodes as in RGH3
# use the fastest diodes you can for this for most reliable timings
#
# DBG_CPU_POST_OUT7 = post bit 0 for RGH 1.2
# DBG_CPU_POST_OUT6 = post bit 1 for RGH 1, 3
DBG_CPU_POST_OUT0 = Pin(POST_PIN_BASE_ID+7, Pin.IN, Pin.PULL_UP) # FT6U8
DBG_CPU_POST_OUT1 = Pin(POST_PIN_BASE_ID+6, Pin.IN, Pin.PULL_UP) # FT6U2
DBG_CPU_POST_OUT2 = Pin(POST_PIN_BASE_ID+5, Pin.IN, Pin.PULL_UP) # FT6U3
DBG_CPU_POST_OUT3 = Pin(POST_PIN_BASE_ID+4, Pin.IN, Pin.PULL_UP) # FT6U4
DBG_CPU_POST_OUT4 = Pin(POST_PIN_BASE_ID+3, Pin.IN, Pin.PULL_UP) # FT6U5
DBG_CPU_POST_OUT5 = Pin(POST_PIN_BASE_ID+2, Pin.IN, Pin.PULL_UP) # FT6U6

DBG_CPU_POST_OUT6 = Pin(POST_PIN_BASE_ID+1, Pin.IN, Pin.PULL_UP)
'''
FT6U7, POST bit 1
'''

DBG_CPU_POST_OUT7 = Pin(POST_PIN_BASE_ID+0, Pin.IN, Pin.PULL_UP)
'''
FT6U1, POST bit 0
'''


# outputs
CPU_RESET           = Pin(CPU_CTRL_BASE_ID + 1, Pin.IN)   # will switch to output later
CPU_PLL_BYPASS      = Pin(CPU_CTRL_BASE_ID,     Pin.OUT)  # via 22k resistor. for EXT_CLK, this pin connects to CPU_EXT_CLK_EN

FAIL_SIGNAL         = Pin(0, Pin.OUT)   # connect this to SMC DBG_LED if the SMC code is hacked to read it

# map pointing postcode -> timeout_in_usec. needed to speedup timeouts
POST_TIMEOUT_TABLE = {
    _make_post(0x22): 10000
}

class GlitchResult(Enum):
    GLITCH_OK = 0
    GLITCH_SMC_TIMEOUT = 1
    GLITCH_SIGNATURE_CHECK_FAILED = 2
    GLITCH_POSTGLITCH_TIMEOUT = 3

# ---------------------------------------------------------------------------------------

def _build_pio_glitch2_posttracker_program(num_toggles_before_irq: int):
    '''
    Builds POST tracker PIO program, needed for single-wire mode.

    Parameters:
    - num_toggles_before_irq: Number of times the POST signal should toggle
                              before raising IRQ.
    - irq_number: IRQ ID to raise.
    '''
    # glitch2 post sequence
    # POST | bit 0 | bit 1
    # -----|-------|--------
    # 0x00 |   0   |   0
    # 0x10 |   0   |   0
    # 0x11 |   1   |   0
    # 0x12 |   0   |   1
    # 0x13 |   1   |   1
    # 0x14 |   0   |   0
    # 0x15 |   1   |   0
    # 0x16 |   0   |   1
    # 0x17 |   1   |   1
    # 0x18 |   0   |   0
    # 0x19 |   1   |   0
    # 0x1A |   0   |   1
    # 0x1B |   1   |   1
    # 0x1C |   0   |   0
    # 0x1D |   1   |   0
    # 0x1E |   0   |   1
    # 0xD0 |   0   |   0
    # 0xD1 |   1   |   0
    # 0xD2 |   0   |   1
    # 0xD3 |   1   |   1
    # 0xD4 |   0   |   0
    # 0xD5 |   1   |   0
    # 0xD6 |   0   |   1
    #
    # POST bit 0 - 20 transitions, ending on 0
    # POST bit 1 - 11 transitions, ending on 1

    @rp2.asm_pio()
    def posttrack():
        set(x, num_toggles_before_irq >> 1)
        wrap_target()
        label("start_over")
        move(y, x)
        label("wait_reset_fall")
        jmp(pin, "wait_reset_fall")
        label("wait_reset_rise")
        jmp(pin, "reset_rose")
        jmp("wait_reset_rise")
        label("reset_rose")
        
        # track 0 -> 1 -> 0 transitions.
        # if /CPU_RESET falls, start over.
        label("wait_post_rise")
        wait(1, pin)
        jmp(pin, "wait_post_fall")
        jmp("start_over")
        label("wait_post_fall")
        wait(0, pin)
        jmp(pin, "decrement_and_repeat")
        label("decrement_and_repeat")
        jmp(x_dec, "wait_post_rise")

        if (num_toggles_before_irq & 1) != 0:
            label("wait_final_post_rise")
            wait(1, pin)
            jmp(pin, "done")
            jmp("start_over")
            label("done")

        # set IRQ to indicate to other statemachine it's time to start running
        irq(irq_number)
        wrap()

    return posttrack

def _build_pio_glitch2_resetter_code( \
                             reset_pulse_width: int,
                             push_after_finish: bool = False,
                             control_pll:    bool = False,
                             use_post_bit_1: bool = False,
                             wait_on_irq: int = -1) -> list:
    '''
    Builds common PIO resetter code for Glitch2-based attacks.

    Parameters:
    - reset_pulse_width: Number of additional cycles to assert /CPU_RESET for. \
                          (0 = 1 cycle, 1 = 2 cycles, etc.) \
                          Value must be between 0 or 31.
    - push_after_finish: If True, runs a `push noblock` instruction once execution finishes.
                         Your script can then pick up on this by running `get()` on the statemachine.
                         Default is False (do not push instruction).
    - control_pll: If True, the PIO program will be built to control CPU_PLL_BYPASS/CPU_EXT_CLK_EN.
    - use_post_bit_1: If True, the PIO program will be built to track POST bit 1 rises/falls.
                      Default is False (track POST bit 0 rises/falls instead).
    - wait_on_irq: Wait for the given IRQ to be set before code runs, then clears it.
      Default is -1 (don't wait).
      Note that the PLL and reset delays will not be repopulated; your script needs to do that.
    '''

    if (0 <= reset_pulse_width <= 31):
        raise RuntimeError("reset_pulse_width must be within 0-31. recommended is 1-3")

    @rp2.asm_pio()
    def resetter():
        if wait_on_irq != -1:
            wait(wait_on_irq, irq, 1)
            irq(clear, wait_on_irq)

        # x = pll delay, if necessary
        # y = reset delay
        if control_pll:
            pull(noblock)
            mov(x, osr)

        pull(noblock)
        mov(y, osr)

        # wait for POST 0xDA
        if use_post_bit_1:
            wait(0, pin, 0) # 0xD8/D9
            if control_pll:
                label("pll_delay")
                jmp(x_dec, "pll_delay")
                set(pins, 1)
            wait(1, pin, 1) # 0xDA
        else:
            wait(1, pin, 0) # 0xD7
            wait(0, pin, 0) # 0xD8
            wait(1, pin, 0) # 0xD9
            if control_pll:
                label("pll_delay")
                jmp(x_dec, "pll_delay")
                set(pins, 1)
            wait(0, pin, 0) # 0xDA

        # reset delay / glitch pulse
        label("reset_delay")
        jmp(y_dec, "reset_delay")
        set(pindirs, 3) [reset_pulse_width] # /CPU_RESET already should be set to 0.

        set(pins, 3)                        # nasty voltage pulse - makes /CPU_RESET rise immediately
                                            # instead of letting it float upward.
                                            # again, this is applying 3v3 to a 1v1 pin!!

        set(pindirs, 0)                     # de-assert all outputs
        if push_after_finish:
            push(noblock)
        
        # spin until PIO restarted
        wrap_target()
        nop()
        wrap()

    return resetter

# ---------------------------------------------------------------------------------------

def _wait_post_transition(current_io_value, timeout_usec=-1) -> tuple | int:
    '''
    Waits for POST transition, timeout or reset.

    Parameters:
    timeout_usec: Optional. Timeout value in microseconds. Default is -1 (do not timeout).

    On success return `(new_io_value, transition_time_in_usec)`

    If operation timed out return -1
    If CPU unexpectedly reset return -2

    '''
    timebase = ticks_us()

    timeout_point = timebase + timeout_usec if timeout_usec >= 0 else -1
    while True:
        t = ticks_us()
        iobits = mem32[RP2040_GPIO_IN] & POST_BITS_MASK
        if iobits != current_io_value:
            if iobits == POST_00:
                return -2
            return (iobits, t - timebase)
        if timeout_point != -1 and t <= timeout_point:
            return -1

def _signal_fail():
    '''
    Pulse FAIL_SIGNAL pin for 1 millisecond.
    '''
    FAIL_SIGNAL.value(1)
    sleep_ms(1)
    FAIL_SIGNAL.value(0)

def _monitor_post_postglitch_glitch2(enable_timeouts=False) -> GlitchResult:
    '''
    Tracks post-glitch boot progress.
    '''
    io = mem32[RP2040_GPIO_IN] & POST_BITS_MASK
    while True:
        wait_result = None
        if enable_timeouts and io in POST_TIMEOUT_TABLE:
            wait_result = _wait_post_transition(io, timeout_usec=POST_TIMEOUT_TABLE[io])
        else:
            wait_result = _wait_post_transition(io)

        if wait_result in [ -1, -2 ]:
            print(f"FAIL: timeout on POST {_unpack_post(io)}")
            _signal_fail()
            return GlitchResult.GLITCH_POSTGLITCH_TIMEOUT

        io = wait_result[0]
        if io in [ POST_10, POST_11 ]:
            print("SUCCESS: XeLL should be running")
            return GlitchResult.GLITCH_OK
        else:
            wait_result = _wait_post_transition(io)
            if wait_result == -1:
                print("FAIL: SMC unexpectedly reset CPU")
                return GlitchResult.GLITCH_SMC_TIMEOUT
            io = wait_result[0]

def _do_glitch2_workflow(pio_sm,
                         fcn_apply_slowdown = None,
                         fcn_cleanup = None,
                         wait_for_pio_resetter_done=False) -> GlitchResult:
    '''
    Common workflow for 8-wire POST Glitch2-based attacks (RGH1.2, EXT_CLK).
    PIO program will always start execution at POST 0xD6.

    Inputs:
    - pio_sm: The PIO statemachine, already initialized and ready to execute.
    - fcn_apply_slowdown: Optional callback executed at 0xD9. Typically where slowdown should be applied.
    - fcn_cleanup: Optional callback to execute once the glitch workflow ends.
    - wait_for_pio_resetter_done: Optional. If True, wait for PIO resetter to finish (your PIO   \
      program should push something to the ISR to indicate it's done). \
      Default is False (waits for 0xDA POST code to change to something else).

    Return values:
    - GLITCH_OK: Success
    - GLITCH_SMC_TIMEOUT - Fail, SMC timeout (slowdown applied too soon, CPU froze, etc.)
    - GLITCH_SIGNATURE_CHECK_FAILED - Fail, signature check failed (reset pulse happened too late
                                    or CPU somehow failed to glitch)
    '''

    print("_do_glitch2_workflow waiting for POST 0xD6")
    while (mem32[RP2040_GPIO_IN] & POST_BITS_MASK) != POST_D5:
        pass

    while (mem32[RP2040_GPIO_IN] & POST_BITS_MASK) != POST_D6:
        pass

    pio_sm.active(1)
    print("0xD6 arrived, started PIO")
    io = mem32[RP2040_GPIO_IN] & POST_BITS_MASK
    while True:
        post_tuple = _wait_post_transition(io)
        if post_tuple == -1:
            print("FAIL: SMC timeout")
            _signal_fail()
            return GlitchResult.GLITCH_SMC_TIMEOUT
        
        # CAUTION! these readings will be skewed by callbacks and behavior below
        print(f"{_unpack_post(io):02x} {post_tuple[1]} usec")

        io = post_tuple[0] # raw value off IO pins, AND masked of course

        if io == POST_D9 and fcn_apply_slowdown is not None:
            fcn_apply_slowdown()

        elif io == POST_DA:
            if wait_for_pio_resetter_done is True:
                pio_sm.get()
            else:
                while (mem32[RP2040_GPIO_IN] & POST_BITS_MASK) == POST_DA:
                    pass
            pio_sm.active(0)
            if fcn_cleanup is not None:
                fcn_cleanup()
            break

    post_after = mem32[RP2040_GPIO_IN] & POST_BITS_MASK

    if post_after == POST_FB:
        print("FAIL: got POST 0xFB")
        _signal_fail()
        return GlitchResult.GLITCH_SIGNATURE_CHECK_FAILED

    if post_after not in [ POST_DB, POST_20, POST_21, POST_22 ]:
        print("BUG CHECK: fcn_cleanup() took too long to execute")
    
    return _monitor_post_postglitch_glitch2()


# ---------------------------------------------------------------------------------------

def rgh12():
    '''
    RGH 1.2, 8-wire POST
    '''

    pll_wait_ms = 0.4
    reset_delay = 349818 # 349821 is the recommended RGH 1.2 delay value

    def _apply_slowdown():
        sleep(pll_wait_ms)
        CPU_PLL_BYPASS.value(1)
        
    def _cleanup():
        CPU_PLL_BYPASS.value(0)

    while True:
        prg = _build_pio_glitch2_resetter_code(4)
        sm = rp2.StateMachine(0,
                              prg,
                              freq = 48000000,
                              in_base=DBG_CPU_POST_OUT7,
                              set_base=CPU_PLL_BYPASS
                              )
        sm.active(0)
        sm.restart()
        sm.put(reset_delay)

        _do_glitch2_workflow(sm, _apply_slowdown, _cleanup)
