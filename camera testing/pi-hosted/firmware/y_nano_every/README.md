# Nano Every Y pulley encoder

This is the active replacement requested for Y. X stays on its original,
separate Nano Every. Do not flash X with this sketch.

| Encoder connection | Y Nano Every pin |
| --- | --- |
| PWM output | **D2 / PA0** |
| Ground | GND |
| Existing confirmed 3.3 V supply | 3V3; retain the encoder's 3.3 V configuration |
| Roof Pi USB | USB data cable to this Y board |

The Nano Every has 5 V GPIO. Do not enable a pull-up into the 3.3 V encoder.
A 3.3 V high is below the ATmega4809's guaranteed 0.7 × VDD high threshold
at a 5 V supply; use a non-inverting 3.3-to-5 V buffer for guaranteed margins.
Never feed a 5 V buffer output back into the encoder. Do not connect the old
Pico or R4 simultaneously as another Y reporter.

D2 is PA0. EVSYS channel 0 routes PA0 to both TCB0 (pulse width) and TCB1
(rising-to-rising period). The timers run at the official core's 16 MHz CPU
clock and capture continuously; this avoids the alternate-frame dead time
of a single timer in combined frequency/pulse-width mode. TCB3 remains the
Arduino core's clock. Capture interrupts only enqueue latched measurements;
angle conversion, unwrapping and USB reporting run in the main loop.

The sketch unwraps accepted AS5048A PWM frames (normally about 1.1 kHz) and
reports position at 100 Hz, preserving 4096 counts/revolution:

```
BM1 Y <sequence> <signed-counts> <idle-or-signal> <epoch-tag>
```

Optional once-per-second diagnostic lines are ignored by the existing Pi:

```
BY1 <valid-frame-count> <period-ticks> <high-ticks> <queue-overruns> <epoch>
```

A one-time startup `BY2 <rises> <falls> <min-period-ticks> <max-period-ticks>`
line comes from an independent 20 ms GPIO polling check, before capture is
enabled. At the 16 MHz clock, a normal 1.1 kHz encoder gives about 22 rising
edges and periods around 14600 ticks in that window. A noisy or wrong signal
can be distinguished from a timer-only problem with this check. After eight
consecutive impossible hardware periods, capture interrupts pause for 100 ms
and retry. This prevents a noisy line from monopolizing the CPU and never
treats rejected frames as valid position feedback.

A 20 ms valid-frame gap or capture queue overflow resets position and changes
the epoch, requiring a new Y zero. EEPROM reserves a new epoch range on each
boot. The built-in LED is steady with valid PWM and blinks otherwise. This
firmware controls no motor, winch or tapper. The Pi retains the half-inch stop
tolerance and quarter-turn reverse threshold.

Build with official Arduino megaAVR core 1.8.8:

```
arduino-cli compile --fqbn arduino:megaavr:nona4809:mode=off firmware/y_nano_every
```

The roof's prepared uploader requires the **Y board's USB serial number**,
refuses the known X board and checks motion/relays are idle. It backs up Y
flash and EEPROM before uploading, verifies flash, invalidates the previous
Y zero and checks fresh serial reports. Its default verification never moves
the winch. Full mechanical accuracy still requires a measured rotation test.

References:
- [Official Nano Every pinout](https://docs.arduino.cc/resources/pinouts/ABX00028-full-pinout.pdf)
- [ATmega4809 timer/event and input specifications](https://ww1.microchip.com/downloads/en/DeviceDoc/ATmega4808-09-DataSheet-DS40002173C.pdf)
