'''
pigli360.py
Common glitching framework because the scattered implementations were getting unmanagable
'''

from machine import Pin,mem32,SoftI2C
from time import sleep, sleep_ms, ticks_us


# POST monitoring must be done as fast as possible
RP2040_GPIO_IN = 0xD0000004

POST_IO_BASE = 15
POST_BITS_MASK = 0xFF << POST_IO_BASE
def _make_post(x):
    return x << POST_IO_BASE

def _unpack_post(x):
    return x >> POST_IO_BASE

# important POST codes
POST_00        = _make_post(0x00)
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

POST_D5        = 0xD5 << POST_IO_BASE
'''
FETCH_CONTENTS_CB_B. copy CB_B from flash into SRAM. setup for 0xD6
'''

POST_D6        = 0xD6 << POST_IO_BASE
'''
HMACSHA_COMPUTE_CB_B. create HMAC key to decrypt CD later on.
CB_A won't touch flash past this point, so this makes it
an ideal point to start our workflows
'''

POST_D9        = 0xD9 << POST_IO_BASE
'''
SHA_COMPUTE_CB_B. typically where slowdown is applied for glitch2
'''

POST_DA        = 0xDB << POST_IO_BASE
'''
SHA_VERIFY_CB_B. executing signature memcmp()
'''

POST_DB        = 0xDB << POST_IO_BASE
'''
BRANCH_CB_B. check passed, jumping to CB_B
'''

POST_20        = 0x20 << POST_IO_BASE
POST_21        = 0x20 << POST_IO_BASE
POST_22        = 0x20 << POST_IO_BASE

POST_FB        = 0xFB << POST_IO_BASE
'''
SHA_VERIFY_CB_B. CB_B hash check failed
'''

# actual logical bits are reversed
# all must be connected to the POST pins via diodes as in RGH3/
# DBG_CPU_POST_OUT7 = post bit 0 for RGH 1.2
# DBG_CPU_POST_OUT6 = post bit 1 for RGH 1, 3
DBG_CPU_POST_OUT0 = Pin(22, Pin.IN, Pin.PULL_UP) # FT6U8
DBG_CPU_POST_OUT1 = Pin(21, Pin.IN, Pin.PULL_UP) # FT6U2
DBG_CPU_POST_OUT2 = Pin(20, Pin.IN, Pin.PULL_UP) # FT6U3
DBG_CPU_POST_OUT3 = Pin(19, Pin.IN, Pin.PULL_UP) # FT6U4
DBG_CPU_POST_OUT4 = Pin(18, Pin.IN, Pin.PULL_UP) # FT6U5
DBG_CPU_POST_OUT5 = Pin(17, Pin.IN, Pin.PULL_UP) # FT6U6
DBG_CPU_POST_OUT6 = Pin(16, Pin.IN, Pin.PULL_UP) # FT6U7
DBG_CPU_POST_OUT7 = Pin(15, Pin.IN, Pin.PULL_UP) # FT6U1

# outputs
CPU_RESET           = Pin(14, Pin.IN)   # will switch to output later
CPU_PLL_BYPASS      = Pin(13, Pin.OUT)  # via 22k resistor. for EXT_CLK, this pin connects to CPU_EXT_CLK

FAIL_SIGNAL         = Pin(0, Pin.OUT)   # connect this to SMC DBG_LED if the SMC code is hacked to read it


GLITCH_OK = 0
GLITCH_SMC_TIMEOUT = 1
GLITCH_SIGNATURE_CHECK_FAILED = 2


# map pointing postcode -> timeout_in_usec. needed to speedup timeouts
POST_TIMEOUT_TABLE = {
    _make_post(0x22): 10000
}

# ---------------------------------------------------------------------------------------

def _build_pio_resetter_code(reset_pulse_width: int,
                             push_after_finish: bool = False,
                             use_post_bit_1: bool = False) -> list:
    '''
    Builds common PIO resetter code.

    Inputs:
    - reset_pulse_width - Number of additional cycles to assert /CPU_RESET for.
                          (0 = 1 cycle, 1 = 2 cycles, etc.)
                          Value must be between 0 or 31.
    - push_after_finish - If True, runs a `push noblock` instruction once execution finishes.
                        Your script can then pick up on this by running `get()` on the statemachine.
                        Default is False (do not push instruction).
    - use_post_bit_1    - If True, the PIO program will be build to track POST bit 1 rises/falls.
                        Default is False (track POST bit 0 rises/falls instead).
    '''
    if (0 <= reset_pulse_width <= 31):
        raise RuntimeError("reset_pulse_width must be within 0-31. recommended is 1-3")
    prg = [
        0x8080, # pull   noblock
        0xa047, # mov    y, osr
    ]

    if use_post_bit_1:
        prg += [
            0x2020, # wait   0 pin, 0   - 0xD8
            0x20a0, # wait   1 pin, 0   - 0xDA
        ]

    else:
        # use POST bit 0
        prg += [
            0x20a0, # wait   1 pin, 0   - 0xD7
            0x2020, # wait   0 pin, 0   - 0xD8
            0x20a0, # wait   1 pin, 0   - 0xD9
            0x2020, # wait   0 pin, 0   - 0xDA
        ]
    
    # kill time until it's time to send the reset pulse
    prg.append(0x0080 | len(prg)) # jmp y--, $ - immediate jump to itself

    # actually send the reset pulse
    prg.append(0xe081 | reset_pulse_width << 8) # set pindirs, 1 [reset_pulse_width]
    prg.append(0xe001) # set    pins, 1
    prg.append(0xe081) # set    pindirs, 1

    if push_after_finish:
        prg.append(0x8000) # push noblock

    prg.append(0xa042) # nop - this is also the wrap instruction.
    return prg

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

def _monitor_post_postglitch(enable_timeouts=False):
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
            print("FAIL: timeout on POST ")
            _signal_fail()

        io = wait_result[0]
        if io in [ POST_10, POST_11 ]:
            print("SUCCESS: XeLL should be running")
            # return immediately
        else:
            wait_result = _wait_post_transition(io)
            if wait_result == -1:
                print("FAIL: SMC unexpectedly reset CPU")
            io = wait_result[0]

def _do_glitch2_workflow(pio_sm,
                         fcn_apply_slowdown,
                         fcn_cleanup,
                         wait_for_pio_resetter_done=False) -> int:
    '''
    Common workflow for glitch2-based attacks.
    PIO program will always start execution at 0xD6.

    Inputs:
    - pio_sm: The PIO statemachine, already initialized and ready to execute.
    - fcn_apply_slowdown: Callback executed at 0xD9. Typically where slowdown should be applied.
    - fcn_cleanup: Callback to execute once the glitch workflow ends.
    - wait_for_pio_resetter_done: Optional. If True, wait for PIO resetter to finish (your PIO   \
      program should push something to the ISR to indicate it's done). \
      Default is False (don't wait).

    Return values:
    - GLITCH_OK - Success
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
    print("POST 0xD6 arrived, PIO has been started")
    io = mem32[RP2040_GPIO_IN] & POST_BITS_MASK
    while True:
        post_tuple = _wait_post_transition(io)
        if post_tuple == -1:
            print("FAIL: SMC timeout")
            _signal_fail()
            return GLITCH_SMC_TIMEOUT

        io = post_tuple[0] # raw value off IO pins, AND masked of course

        if io == POST_D9:
            fcn_apply_slowdown()

        elif io == POST_DA:
            if wait_for_pio_resetter_done is True:
                pio_sm.get()
            
            pio_sm.active(0)
            fcn_cleanup()
            break

        print(f"{_unpack_post(io):02x} {post_tuple[1]} usec")

    post_after = mem32[RP2040_GPIO_IN] & POST_BITS_MASK

    if post_after == POST_FB:
        print("FAIL: POST 0xFB encountered")
        _signal_fail()
        return GLITCH_SIGNATURE_CHECK_FAILED

    if post_after not in [ POST_DB, POST_20, POST_21, POST_22 ]:
        print("BUG CHECK: fcn_cleanup() took too long to execute")
    
    return _monitor_post_postglitch()

# ---------------------------------------------------------------------------------------

def rgh12():
    pass