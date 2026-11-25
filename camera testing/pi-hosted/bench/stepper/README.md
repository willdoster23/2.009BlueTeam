# Stepper Bench on the Mac

Dedicated Nano Every firmware and a local browser controller for the 12 Nm
HBS86H kit. This is separate from the deployed roof/robot app. The existing
X and Y firmware is not modified. No command runs automatically on boot,
connection, page load or reconnection.

Installed November 22, 2026 on the dedicated motor Nano Every, USB serial
`498847005154524C4A202020FF0E4352`. Flash verification and passive reporting
passed: 59 status reports at 19.92 Hz, disarmed, stationary and zero commanded
pulses. The original flash, EEPROM and fuses are backed up under `backups/`;
`runtime/installed.json` records source and HEX checksums. No motor motion or
lifting test was performed. The Pi software remains unchanged.

Updated November 22: removed the bench distance/duration caps and added the
large red E-STOP beside the move buttons. The updated Nano firmware was uploaded
and flash-verified with 48 V off, then passively checked with 59 reports at
19.92 Hz, disarmed and zero pulses. The prior bench firmware is backed up under
`backups/20261122T212155Z-498847005154524C4A202020FF0E4352/`. All 17 controller
tests and the pulse-engine tests passed, including exact moves above 2,000,000
pulses and beyond 60 seconds. The UI E-STOP command was verified against the
connected, disarmed Nano without a powered motion test.

Double-click **Start Stepper Bench.command**, then open http://127.0.0.1:8766/.
Use **Stop Stepper Bench.command** to close the local server. Python 3 with
`pyserial` is required; it is already available on this Mac. CSV export retains
the last 30 minutes at the firmware's 20 Hz reporting rate.

If a button reports **Page expired**, refresh the browser page. Restarting the
local server changes its page connection key; an older open tab can still show
telemetry but cannot send commands. Refreshing never arms or starts a move.

## Motor Nano wiring

Use the Nano Every labelled for this motor, not either existing X/Y board.
USB connects the motor Nano to the computer and supplies its 5 V power.

| Nano Every | Driver |
| --- | --- |
| D9 | PUL+ |
| D8 | DIR+ |
| GND | PUL- |
| GND | DIR- |

No MOSFET modules are needed with this Nano Every interface. Do not attach a
5 V Arduino output directly to a Raspberry Pi GPIO; the later Pi connection
will be USB. Do not connect 48 V to any Arduino pin, including VIN.

Keep ENA+ and ENA- unconnected. Stop ends STEP pulses and keeps the driver
enabled; it does not command a freewheeling motor. Loss of power, a driver
fault or inadequate torque can still release a load. This controller is for
bench validation, not a substitute for a power-off mechanical brake/restraint.

Supply V+ and V- go across the two AC-labelled driver power inputs; on the
AC/AC variant DC polarity is independent. Motor phases and the motor's
encoder remain attached to their appropriate driver terminal groups. Never
connect/disconnect motor or encoder leads with the driver powered.

## Optional driver status wires

With power off, connect ALM+ to D2 and ALM- to Nano GND; PEND+ to D3 and PEND-
to GND. The sketch uses internal 5 V pull-ups for these isolated open-collector
outputs. Do not feed external 24 V into the Nano. Unconnected inputs read HIGH;
that alone is not evidence of a fault or a position-reached signal.

Different HBS86H variants have configurable output polarity. The dashboard
shows raw levels. Alarm stopping is disabled until the operator chooses and
verifies HIGH=FAULT or LOW=FAULT. Apply this setting while disarmed. A configured
alarm is debounced for 10 ms before an independent firmware stop.

Neither ALM nor PEND reports the built-in encoder angle/count. The motor's
encoder is closed-loop feedback inside the driver. The display and log clearly
separate **commanded pulse position**, **estimated cable travel**, and the
optional independent external encoder measurement. There is no torque or
current sensor in this setup.

## Add the existing external Y encoder later

Connect the existing Y Nano Every, with its AS5048A sensor and existing Y
firmware, to a second Mac USB port. Its known USB serial is
`73F97AA75154524C4A202020FF0C3720`. Select it under External Y Encoder. The
application reads the existing `BM1 Y` 100 Hz serial protocol without writing
firmware or commanding this board. X USB serial
`D92DB2CC515452344D202020FF094441` is protected from motor selection.

Zero the external encoder after a valid signal appears. Enter measured inches
of cable travel per encoder revolution if inch feedback is desired. This is
the pulley calibration, not automatically the motor drum circumference. With
no calibration the app shows encoder turns. Re-zero after signal loss or an
encoder epoch reset. Optional encoder monitoring stops and disarms a move on
stale/invalid feedback or reference reset; it does not automatically correct
distance, diagnose a stall, or enforce physical travel limits.

## First test

1. Secure and unload the motor. Confirm the D9/D8 wiring, driver phase/encoder
   wiring, signal-input voltage selection if present, and matched motor settings.
2. Set Pulses/revolution to the value on the actual driver's DIP table. The
   dashboard's 1600 and 4-inch defaults are assumptions, not detected settings.
3. Connect the motor Nano in the dashboard. Check fresh telemetry. No movement
   is requested by connection or zeroing.
4. With the power terminals secure and covered, apply the motor supply power.
   Click Arm. Start with 0.125 motor revolution at 3 RPM and 10 RPM/s acceleration.
   Press Move positive. Measure the rotation and determine the lifting direction.
5. Try the matching negative move. Use STOP PULSES or Escape to stop and disarm.
   Confirm Stop, browser closure and USB communication loss before any loaded
   validation. Use an independent restraint for any lifted test weight.
6. Add the optional status and external encoder connections, verify polarity
   and measurement scale, then export the CSV for comparison to measured travel.

Moves run to the requested pulse count without the former 10-revolution,
100-unit input, 2,000,000-pulse or 50/60-second bench caps. The command and
cumulative position use signed 32-bit counters: at most 2,147,483,647 pulses
per move, with a cumulative position between -2,147,483,648 and 2,147,483,647.
Zero the commanded position when needed to stay within that representation.
The 5,000 pulses/s timing limit, 800 ms serial heartbeat deadline, 50 us STEP
high pulses and 20 us direction setup remain. The large red **E-STOP** beside
the move buttons uses the same immediate pulse-stop/disarm path as the header
Stop button and Escape; it remains available during a command request.
The browser heartbeat expires after 550 ms;
Nano stopping therefore occurs within roughly 1.4 s of complete page loss
even if the final Stop request is lost. Closing/hiding the page also requests
an immediate stop. USB loss stops pulses within the Nano's 800 ms deadline.
Ordinary Stop is abrupt. The watchdog resets a stalled main loop; restart is
disarmed. No ENA/brake hardware is switched.

## Build / upload

The firmware is `../../firmware/winch_nano_every_bench/`. Build with the
official Arduino megaAVR 1.8.8 core, FQBN
`arduino:megaavr:nona4809:mode=off`. `program.py --serial ...` backs up flash,
EEPROM and relevant fuses, refuses the existing X/Y boards, then uploads and
verifies without issuing ARM or MOVE. Disconnect the 48 V supply for programming.

Tests: `python3 -m unittest discover -s tests -v`. Compile/run
`tests/motion_core_test.cpp` with a host C++11 compiler. These tests do not move
hardware. Actual lifting torque, encoder accuracy and stopping distance still
require physical measurement.

Sources: [Nano Every pinout](https://content.arduino.cc/assets/Pinout-NANOevery_v1.pdf),
[Nano Every ratings](https://store.arduino.cc/products/nano-every),
[seller driver specifications](https://www.amazon.com/dp/B0DSDY978W),
[HBS86H manual](https://cuahangvattu.com/storage/document/hbs86h-manual-en.pdf).
