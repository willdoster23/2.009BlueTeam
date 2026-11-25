# BrickMapper stepper update — live on both motor Nanos

Deployed November 24, 2026, with user-confirmed 48 V power disconnected and both axes supported/unloaded. Both prepared images were written and verified on their fixed USB identities; both reported disarmed, stopped, with zero commanded pulses. The roof controller now uses `--stepper-axes` via `stepper-axes.conf`. The separate pulley encoder firmware was not programmed and its signal was verified live. No motor motion was tested. Re-zero both axes and confirm driver pulse scaling/travel calibration before distance moves. The standalone motor bench server remains stopped.

## Hardware and position sources

| Axis | Hardware | Position source |
| --- | --- | --- |
| X | STEPPERONLINE 23HS32-4004S, 2.4 N·m, 4 A/phase, 8 mm shaft; separate EASON DM542 driver and repurposed original X Nano Every | Commanded motor steps, explicitly marked **estimated**. No encoder or skipped-step detection. |
| Y motor | Actual label 86HE250-156B; HBS86H V4 driver; existing dedicated Y motor Nano Every | Commanded steps/RPM shown for diagnostics. The driver closes its own loop using the motor encoder. Its actual rotor counts are not exposed by STEP/DIR. |
| Y cable | Existing external pulley encoder and its separate Nano Every | Sole source of measured cable position and cable speed. Its existing firmware and D2 wiring are unchanged. |

References: [X motor manufacturer](https://www.omc-stepperonline.com/nema-23-stepper-motor-2-4nm-339-79oz-in-4a-57x57x82mm-8mm-shaft-4-wires-23hs32-4004s),
[user-selected X driver](https://www.amazon.com/dp/B08WLLJD34).
The EASON listing specifies 20–50 VDC. A nominal 48 V supply is close to that
upper limit: verify the actual supply voltage and applicable driver label.
Y's wider voltage rating does not increase X's rating. Current/microstep DIP
settings come from each actual driver, not from software or the other driver.
No electrical power rating or torque performance has been validated by this update.

## Controls

**Current November 25 handoff:** the one-second basic-test buttons were removed and the live Manual page restored to distance-in-inches movement with RPM controls. Normal distance moves still use their existing ramp and calibration/zero checks. The 100 µs firmware and backend diagnostic capability remain available internally; historical basic-test instructions below do not describe the current operator page. The X motor still ticks without confirmed rotation even unloaded and previously overheated; no successful physical X motion or scope verification is claimed.

November 25 diagnostic follow-up adds `basic-test`: a one-second pulse train at the selected RPM, represented internally as a finite number of pulses and `MOVE ... acceleration=0`. Firmware starts directly at that rate and skips the ramp. No distance scale, zero or pulley sample gates this command. The transport requires the new firmware's `SBC FIXED=1 PULSE_US=100` capability before permitting acceleration zero. STEP high time is now 100 µs on both axes; the supported 5000/6000 pulses/s limits retain a low interval. Firmware tests check edge count, two-tick high width, fixed rate without ramp and heartbeat stop; backend checks cover an uncalibrated/unzeroed Y with its encoder absent. Actual electrical waveform and shaft rotation still require physical observation.

November 24 manual-control follow-up is live: speed (RPM), Apply speed and Arm/Disarm are beside the primary inch-distance/direction controls on Manual. Applying speed disarms only that axis, saves only RPM using the current applied geometry, and preserves zero. Revolution tests and advanced driver settings start collapsed. Readiness text explains missing calibration, zero, encoder or arming instead of leaving a disabled direction button unexplained. The user confirmed the original 24 mm X pinion and direct drive; its existing 2.968434 in/turn estimate was confirmed on the running controller with X zero preserved. Y's unconfirmed planning calibration was left for the operator; pulley measurements are unchanged. This follow-up updated only three frontend files; no controller restart or motor movement was needed.

- Setup has independent X/Y pulses/revolution, motor RPM, acceleration,
  travel/revolution and direction. These settings persist on the rooftop Pi.
- Default pulse scaling is 1600 pulses/revolution, 3 RPM and 10 RPM/s.
  The default X travel, 2.968434 in/revolution, assumes the existing 24 mm
  pitch-diameter pinion is directly on the new shaft. Y's 12.566371 in/revolution
  is a nominal 4-inch winding diameter used only to plan motor pulses.
  Both travel assumptions require confirmation in Setup before inch moves.
- Y's separate pulley calibration still determines actual Y distance. It must
  be set to the pulley mechanism, not the winch drum. Encoder direction is editable.
- Manual has separate Arm buttons, positive/negative motor-turn tests, and
  large red E-STOP buttons alongside the main controls. Motor-turn tests do
  not require the external pulley encoder. Y moves in inches and automatic
  routes require fresh pulley feedback, a Y zero and confirmed motor travel setup.
- E-STOP and Escape stop both stepper axes and disarm commands. They stop pulses;
  they do not switch driver enable, power or a mechanical brake. Normal finite
  moves leave the page armed for another move. Page hiding/closure stops and
  disarms both axes; a new page/reconnection never automatically arms or moves.
- Y has a **200 RPM software cap**, while its default speed stays 3 RPM.
  At 1600 pulses/revolution, 200 RPM needs about 5333.33 pulses/s. The prepared
  Y firmware supports up to 6000 pulses/s; X remains at 5000 pulses/s.
  Finer pulse scaling can lower the available RPM below 200. The UI and backend
  enforce the lower of the timing limit and Y's 200 RPM cap. These limits do not
  establish the motor's physical speed or torque capability.
- No former 10-turn, 100-unit, 2-million-pulse, or 50/60-second bench cap is
  applied to stepper moves. Each command and cumulative motor position still
  obey signed 32-bit counter limits.
- X calibration/zero must be checked physically. Changing RPM alone preserves X
  zero; changing its pulse scaling, travel or direction requires a new X zero.

## Independent encoder timing and Pi workload

Each motor Nano reports at **20 Hz (one report every 50 ms)**. Its independent
20 kHz timer generates STEP pulses and checks its own serial heartbeat deadline;
Pi telemetry timing does not generate individual pulses. The existing separate
pulley encoder remains at **100 Hz**, with its firmware and capture timing unchanged.
The host drains USB every 5 ms using nonblocking reads and bounded batches.
Two motor reports plus the pulley are approximately 140 position reports/second
in total. The browser and existing network status cache remain at 2 Hz; this
change does not increase their polling rate. These code-level rates have been
verified locally; roof Pi CPU utilization and end-to-end latency have not been
measured on hardware during this update.

Timestamps/counts from the independent boards are never required to match.
Cable speed uses a rolling pulley-only window; commanded motor speed is separate.
Transient pulley gaps retain the last valid sample/reference for up to one
second. A completed motor command allows at least 300 ms for a newer pulley
sample before judging the final half-inch target window. Persistent loss, an
actual encoder reference reset, sustained reverse/no-progress travel, an
optional configured ALM fault, or a route lower-limit violation stops a measured
move once and disarms. There is no repeated alert for normal arrival-time differences.
X has no measured-motion verification. Raw unconnected ALM/EX inputs do not
prove that a motor is healthy; ALM interpretation defaults to off.

## Wiring for the later installation

Use **two motor Nano Every boards**, one per driver. Keep the existing Y motor
Nano and its D9/D8 wiring; repurpose the original X motor Nano for the new X
stepper. The separate pulley encoder Nano remains unchanged, so there are three
USB boards in total. USB port names/order do not choose the axis: firmware
identifies X or Y explicitly, and the upload helper checks each board's USB serial.
Old DC-motor X firmware is not accepted as a stepper controller.

| Motor Nano | Pin | Driver connection |
| --- | --- | --- |
| Original X Nano, repurposed | D9 | X DM542 PUL+ |
| Original X Nano, repurposed | D8 | X DM542 DIR+ |
| Original X Nano, repurposed | GND pin(s) | X DM542 PUL− and DIR− |
| Existing Y motor Nano | D9 | Y HBS86H PUL+ |
| Existing Y motor Nano | D8 | Y HBS86H DIR+ |
| Existing Y motor Nano | GND pin(s) | Y HBS86H PUL− and DIR− |

Use the actual drivers' signal-input specifications. Connect both motor Nanos
and the separate pulley encoder Nano by USB to the rooftop Pi. Leave driver
enable inputs unconnected as specified by their manuals. No motor supply
voltage connects to an Arduino pin. The old shared-board X D10/D7 wiring is
superseded; use D9/D8 on the X Nano as shown above.

On the Y motor Nano only, optional isolated outputs: ALM+ → D2, ALM− → GND;
EX+ → D3, EX− → GND. These use that Nano's internal 5 V pull-ups; do not add
an external high-voltage pull-up. Verify polarity before selecting an ALM interlock.
X's DM542 has no encoder feedback through STEP/DIR and uses no D2/D3 inputs.

Connect X's supply to its DC V+/V− terminals with correct polarity. Connect Y
according to the actual HBS86H AC/DC-labelled supply terminals. This software
change does not wire, reconfigure or energize the shared supply.

## Prepared firmware and upload later

`firmware/brickmapper_stepper/hex/` contains separate **X.hex** and **Y.hex**
images and their SHA-256 manifest. Each board identifies only its own axis,
boots disarmed, and owns its own commanded counter and 800 ms heartbeat
watchdog. BrickMapper enforces one moving axis at a time across the two USB
ports. E-STOP sends STOP to both motor boards.

The existing Y bench firmware is still recognized on the same Y motor Nano;
it does not need replacing just to separate the boards. **For 200 RPM at 1600
pulses/revolution, upload the new Y image later**: the earlier bench firmware's
5000 pulses/s ceiling is below the required 5333.33 pulses/s. The standalone
bench controller itself has not been changed or restarted.

The upload helper checks the selected axis against a fixed USB identity and
image/source checksums, backs up flash/EEPROM/fuses, uploads with flash
verification, and checks passive disarmed/zero reports for that one axis.
It never sends ARM or MOVE and never programs the other motor board.

Known USB identities:

| Board | USB serial | Upload image |
| --- | --- | --- |
| Original X motor Nano — repurposed for X stepper | D92DB2CC515452344D202020FF094441 | X.hex |
| Existing dedicated Y motor Nano | 498847005154524C4A202020FF0E4352 | Y.hex |
| Existing pulley encoder Nano — **do not overwrite** | 73F97AA75154524C4A202020FF0C3720 | Existing encoder firmware |

1. Disconnect the 48 V motor supply; unload/support the mechanism. Leave USB on.
   Close serial monitors and the bench controller. On the roof Pi, stop
   `blueteam-network.service` before programming.
2. Install Arduino CLI, the Arduino megaAVR core and Python `pyserial` if needed.
   The prepared X/Y images were built with megaAVR 1.8.8 and FQBN
   `arduino:megaavr:nona4809:mode=off`. Do not select Nano R4 or classic Nano.
3. From this `pi-hosted` folder, run:

```sh
python3 tools/upload-stepper-firmware.py --axis X --serial D92DB2CC515452344D202020FF094441 --power-disconnected
python3 tools/upload-stepper-firmware.py --axis Y --serial 498847005154524C4A202020FF0E4352 --power-disconnected
```

4. Copy this updated source folder to the rooftop Pi. For its existing base
   installation, run `sudo bash pi/install-roof.sh YOUR_PI_USER --stepper-axes`.
   This selects stepper control and does not initialize the VEVOR relay outputs.
   A normal installer update preserves the selected mode; `--relay-winch`
   explicitly restores the prior relay controller. Do not run the robot installer
   just to change rooftop motion.
5. With motor power still off, open BrickMapper and confirm X reports from its repurposed Nano, Y reports from its existing motor Nano,
   and independent pulley encoder reports. Set pulse scaling to each driver's actual DIP
   setting. Confirm direction and travel calibration. Reconnect motor power only
   when ready for an unloaded physical direction/zero/distance check.

To rebuild both separate motor images without touching USB:
`python3 tools/build-stepper-firmware.py`.

## Verification

Python controller tests include measured Y versus motor commands, estimated X,
role-separated serial routing, large moves, stop cancellation, browser leases,
short versus persistent pulley gaps, reference resets and delayed encoder arrival
after motor completion. Existing relay/X-zero/automatic-route tests remain in place.
Both dedicated Nano firmware variants compile. Their actual command parser is
also tested with simulated I/O for axis identity, D9/D8, pulse timing, limits,
STOP, independent watchdogs and Y-only alarm behavior. Host tests cover distinct
USB command routing, the single-axis interlock, wrong-role rejection, old X
firmware rejection and existing Y bench compatibility. The existing isolated browser simulator supports
arming, X movement, separate readouts, saved settings and E-STOP during Y movement.
These checks do not validate real torque, skipped steps, supply margin, braking,
or physical stopping distance. No Pi deployment or motor-board upload was performed. Saved photo metadata preserves X as
estimated and Y as pulley-measured, including backup/restore.

Optional simulator: `BRICK_PREVIEW_STEPPERS=1 python3 preview/serve.py`.
It opens no USB/GPIO or Pi connections. Stop it with Ctrl-C when finished.
