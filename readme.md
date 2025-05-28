# pigli360 - Experimental/research RGH stuff for Raspberry Pi Pico

Yeah.

## A helpful note

Nothing in here produces a useful glitcher as-is. The code is buggy and kinda shitty
and I'm not able to get any useful results with it. This is more experimental than
anything; maybe I can make it work, maybe not. Either way it's not useful for modding
your Xbox.

Other people have managed to produce RGH1.2 implementations on RP2040, so if you want
to make a RP2040-based glitcher, go looking for those.

## So why try doing this?

RGH3 can be slow on phats and tends to be super unreliable on Jaspers. Meanwhile,
RGH1.2 requires a modchip that, while it's able to work more precisely, still has
shortcomings. And still sucks on Jaspers.

Glitch chips are mainly based around an outdated and almost certainly end-of-life Xilinx
FPGA, and they tend to cost a lot more than what a hypothetical modern solution would.
They also don't monitor the full POST bus, so when a boot fails, the glitch chip is forced
to sit around drooling like an idiot before the SMC gives up and reboots the system.

Also I wanted to write this implementation in Micropython to show that you can write a working
RGH implementation in slow ass software. RGH3 demonstrated that software-based glitching is
viable, and it's quite possible that someone could launch a successful attack on a modern 48 MHz
Cortex microcontroller, no FPGA required. So yeah, this is just a shitpost version of RGH.
