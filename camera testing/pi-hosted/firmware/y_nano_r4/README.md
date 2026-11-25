# Nano R4 pulley encoder

This replaces the Y Pico 2 only. Leave the X Nano Every and roof relay wiring alone.

| Connection | Nano R4 connection |
| --- | --- |
| AS5048A encoder supply | 3V3, retaining the confirmed 3.3 V configuration |
| Encoder ground | GND |
| Encoder PWM signal | 3.3-to-5 V non-inverting logic buffer input; buffer output to **D2** |
| Roof Pi USB port | Nano R4 USB-C, with a data cable |

The Nano R4 GPIO runs at 5 V. Its specified input-high minimum is 0.8 × VCC,
or 4 V at a 5 V supply. Direct 3.3 V PWM into D2 is not guaranteed to work.
A 5 V-powered 74AHCT125 is one suitable buffer: PWM to 1A (pin 2), 1Y
(pin 3) to D2, 1OE (pin 1) to GND, VCC (pin 14) to Nano 5V and GND
(pin 7) to the common ground, with a 100 nF bypass capacitor at its supply.
Disable unused buffer channels and tie their inputs to ground. The encoder
remains powered at 3.3 V. Do not connect a 5 V buffer output to the encoder
or Pico. D2 is an input only; its internal pull-up is disabled.

D2 maps to P105 / GPT1A in the official Nano R4 core. GPT1 captures PWM
period on rising edges and high time on falling edges, using hardware capture
and counter clear. The sketch unwraps every accepted AS5048A frame (normally
about 1.1 kHz) and reports accumulated position every 10 ms (100 Hz) over USB:

```
BM1 Y <sequence> <signed-counts> <idle-or-signal> <epoch-tag>
```

It preserves 4096 counts/revolution and the Pico's PWM conversion. A 20 ms
loss of valid frames resets position and changes the epoch, invalidating the
old roof zero. EEPROM reserves a new epoch range at each boot. Valid signal
makes the built-in LED steady; missing signal makes it blink. No winch,
motor or tapper output exists in this sketch. The existing Pi controller
continues to own the half-inch stop tolerance and quarter-turn reverse guard.

Compile with the official Arduino Renesas UNO core, version 1.5.1 or later:

```
arduino-cli compile --fqbn arduino:renesas_uno:nanor4 firmware/y_nano_r4
```

The firmware must be flashed onto the Nano R4; copying it to the Pi alone
does not program the board. Use the IDE with **Arduino Nano R4** selected,
or the prepared firmware binary and the board's DFU bootloader. Disconnect
the old Y Pico so only one board reports role Y. Restart the roof controller
after programming and set Y zero again when the valid signal is restored.

Official references:
- [Nano R4 pin mapping](https://github.com/arduino/ArduinoCore-renesas/blob/main/variants/NANOR4/variant.cpp)
- [Renesas RA4M1 input voltage limits, table 2.4](https://www.renesas.com/en/document/dst/ra4m1-group-datasheet?r=1168071)
- [Renesas GPT input capture](https://renesas.github.io/fsp/group___g_p_t.html)
- [TI SN74AHCT125](https://www.ti.com/product/SN74AHCT125)
