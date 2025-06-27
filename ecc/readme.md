# XeLL ECC images directory

Bad ideas need explanations, so let's go.

## "Glitch3" ECCs

These are RGH3-like images with a normal glitcher-based hacked SMC program.

Glitch3 was originally created with the assumption that some of the trial attacks kept crashing during
hardware init (POST 0x22, 0x23) because the memory/security units were too unstable coming out of the
reset glitch and needed a delay to stabilize. RGH3's CB_X provides such a delay so I tested it against
that to no success. The CPU kept crashing.

But it turns out CB_X can be very potent with the normal PLL and CPU_EXT_CLK attacks for a couple of reasons.

1. CB_X takes next to no time to load, so the glitch can run a lot sooner.
2. POST behavior during CB_X is fairly predictable. CB_X will output POST 0x54 when the payload has been
   moved to safe SRAM and it's delaying execution before loading CB_B. If the CPU crashes during a failed
   glitch attempt, it will usually do so at POST 0x54.

As such, this can speed up glitch attacks such that a boot can be attempted once per second.
For comparison, CR4 takes about 4 seconds to retry and SMC+ takes about two.

HOWEVER:

There is a good chance the CPU will still crash in CB_B at 0x2E (HWINIT). This is the typical behavior on
Jaspers. Therefore there is a chance that Glitch3 won't actually speed the boot up much in practice.
Credit to Octal for this info.
