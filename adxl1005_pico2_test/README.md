# ADXL1005 + Pico 2 tapper test

Standalone test prepared November 23, 2026. This does not change BrickMapper,
the roof Pi, the motor Nanos, or the solenoid controls. Connect the Pico 2 by
USB directly to the laptop. The recorder never fires the tapper.

The sensor will be mounted on the moving tip/plunger. The default moving mass
is **73 grams**, as supplied by the user. If that weighing excluded the sensor,
bracket or other moving pieces, add their mass before interpreting the force estimate.

## Wire the evaluation board

Use the **signal names printed on the sensor PCB**, not the IC's pin numbers.
This wiring is for an EVAL-ADXL1005Z-style board powered directly at 3.3 V.

| Sensor board label | Pico 2 signal | Pico physical pin |
| --- | --- | --- |
| VDD | 3V3(OUT) | 36 |
| GND | AGND | 33 |
| Vout / OUT | GP26 / ADC0 | 31 |
| ST | GND | 33 |
| STBY / STANDBY | GND | 33 |
| ORout / OR, recommended but optional | GP15 | 20 |

Use a small ground junction for GND, ST and STBY if needed. USB powers the Pico.
**Do not use VBUS/5 V for the sensor in this direct-input wiring.** The sensor
supports a 3.3 V supply; its analog signal and overrange output can then connect
to the Pico's 3.3 V inputs. Retain the evaluation board's fitted output filter.
Do not connect a solenoid coil terminal to a Pico pin.
[ADI board schematic](https://wiki.analog.com/_media/resources/eval/user-guides/inertial-mems/accelerometers/301-884_adxl100xz_evaluation_board_-_schematic.pdf),
[Pico pin documentation](https://www.raspberrypi.com/documentation/microcontrollers/pico-series.html).

Bolt the board rigidly to the moving assembly with its sensitive-axis arrow
parallel to travel. It measures one in-plane axis. Strain-relieve flexible wires,
keep them clear of the stroke, and route the analog signal away from coil wiring.
The current zip-tie/tape mounting is temporary: sensor-board movement can add
apparent peaks or ringing. The 3 N counting cutoff does not correct those peaks.
Treat force estimates as approximate until the board is fixed rigidly near the sensor.
[ADXL1005 data sheet, mounting and axis diagram](https://www.analog.com/media/en/technical-documentation/data-sheets/adxl1005.pdf).

## Install the test firmware on the Pico 2

1. Use the non-wireless **Pico 2 / RP2350**. The included image is not for the
   original Pico/RP2040, Pico 2 W, or an Arduino Nano.
2. Hold BOOTSEL while connecting USB. Release it when the boot drive appears.
3. Copy **`firmware/prebuilt/adxl1005_test.uf2`** to that drive. This replaces
   the program on that Pico; no Python file needs copying to the Pico afterward.
4. Reconnect USB if necessary. The test program appears as a USB serial device.
   It boots idle and waits for a laptop request; it controls no solenoid output.

The firmware image is compiled and included for convenience. It has not been
flashed to a board or tested against the physical sensor during preparation.

## Graphical recording (recommended)

On a Mac, double-click **`Tapper Test.app`** or **`Run Test.command`**. Keep the
app beside the Python files in this folder. Python 3.10 or newer with Tkinter is
required (the python.org macOS installer includes it). The first launch sets up
local dependencies if needed; no terminal port entry is required.

1. Select your Pico in the **Pico device** dropdown. It refreshes automatically;
   **Show all serial devices** is available if its USB description is unusual.
2. Click **Connect**. Only the selected device is probed. The test firmware must
   identify itself before recording is enabled. Connect does not arm a capture.
3. Set **Moving mass** (default 73 g), **Trigger level** (default 2 g), and
   **Recording period** (default 30 seconds, adjustable from 1 to 120 seconds).
   Check **ORout connected to GP15** only when that wire is present.
4. Click **Start recording**. Keep the tip still for calibration. When the
   window says **ARMED**, switch to BrickMapper and fire the tapper there.
   The recorder does not control or stop the solenoid.
5. Only captures with a **peak absolute inertial-force estimate of at least 3 N**
   count as taps. Weaker captures are ignored without an error and immediately
   re-arm; they do not appear in the tap count, graphs or exported tap CSV.
   At the default 73 g, 3 N corresponds to about 4.19 g of axial acceleration.
   The 2 g acquisition trigger stays lower to preserve the start of the impulse.
   It waits **2 seconds after each accepted tap** before re-arming, giving rebound
   and shaking time to settle. Change **Pause after capture (s)** in Capture
   settings (0–10 seconds) if needed. This pause does not change the saved waveform.
   Wait for **ARMED** before the next tap: there are
   brief transfer/calibration gaps, so arbitrary back-to-back bursts may be missed.
   The session ends after the selected period, or click **Stop & save** to end early.
   A tap already in progress may finish before the final save. Completed taps
   remain available if you stop the session or the Pico disconnects.
6. Select **All taps** for an aligned comparison, or an individual tap for its
   full trace. Use the plot toolbar to zoom. **Export CSV** saves the selected
   tap or the full session; **Export graphs** saves the current view as PNG, PDF
   or SVG. **Open saved results** opens the session folder.

The Pico samples each tap at **250,000 samples/second**, retaining 20 ms before
its acceleration trigger and 180 ms afterward by default. Capture settings can
change the per-tap window (4–240 ms), pretrigger time, and voltage/sensitivity
calibration. The laptop remains responsive while the recorder waits for taps.
The existing Pico test UF2 remains compatible; this graphical update does not
require another firmware upload if that UF2 is already installed.

**A timed session consists of separate triggered tap windows, not a continuous
trace of the quiet intervals.** Each tap's CSV time starts relative to its own
trigger; the combined CSV includes a tap number. Across-tap host receive times
are diagnostic metadata, not precise impact timestamps. Overview graphs use
min/max display envelopes to remain responsive and retain peaks. Individual-tap
views and CSV retain the full-rate samples.

**Counted taps are not a verified count of physical taps.** Shaking or
electrical noise can also cross the trigger level. The settling pause reduces
repeat triggers from one hit; if unwanted captures remain, increase the trigger
level above the unwanted motion while keeping it below the real tap's peak.
The recorder cannot guarantee that every capture represents a solenoid tap.

Each session is automatically saved under `captures/session-.../`:

- `all_taps.csv`: every sample from every accepted tap (peak at least 3 N), with tap number,
  relative time, ADC value, acceleration, inertial-force estimate and flags.
- `session_graphs.png`: comparison graphs aligned to each acceleration trigger.
- `session.json`: period, accepted tap count, number of ignored weak captures,
  3 N cutoff, settings, stop/error state and gap notes.
- `tap-001/`, `tap-002/`, etc.: original `raw_adc_u16le.bin` and `capture.json`
  for each tap, including calibration, mass and checksum.

**Preview sample data** generates three explicitly labeled simulated taps,
without opening any USB port. Those results are saved under `captures/SAMPLE-.../`.
Nothing in the recorder adds a service, USB task or HTTP polling to the roof Pi.
The raw signal is not digitally filtered. Hardware filtering, mounting and ADC
performance still limit measurement quality; software checks do not calibrate it.

## Optional terminal use

The original single-tap command-line recorder is still available:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python capture.py --list
python capture.py --port /dev/cu.usbmodemYOUR_PICO --or-wired
```

This waits up to 30 seconds for one tap and saves a single `samples.csv`, plot,
raw file and JSON. Omit `--or-wired` if ORout is not connected. Ctrl-C cancels that
measurement; it does not stop the separate solenoid controller.

## What the force trace means

With fixed sensor orientation, a stationary baseline removes DC offset and
constant gravity from the plotted acceleration. The program then calculates:

```text
inertial-force estimate (N) = moving mass (kg) × axial acceleration (m/s²)
73 g moving mass:           = 0.71588545 × acceleration (g)
```

**This is not a direct measurement of wall/contact force.** The moving tip also
experiences solenoid, spring, friction and gravity forces. Those contributions
must be modeled or calibrated to infer contact force. A calibrated load cell or
instrumented impact reference is needed to validate a contact-force result.
The plots/CSV deliberately call the result an inertial-force estimate.

The sensor's nominal range is ±100 g. A hard impact can exceed that even if the
ADC never reaches a rail. The software flags near-range samples and captures OR
activity. OR indicates severe overload; absence of OR does not guarantee that a
100 g peak was measured faithfully. Treat clipped peaks as incomplete data.
[ADXL1005 range and overrange behavior](https://www.analog.com/media/en/technical-documentation/data-sheets/adxl1005.pdf).

Sensitivity defaults to the nominal 20 mV/g at 5 V scaled to the 3.3 V supply:
13.2 mV/g. Enter measured supply/reference values and calibrated sensitivity if
available. Software checks do not calibrate the ADC, prove sensor connectivity,
or establish force accuracy.

## Useful options

```sh
# Different total moving mass, including sensor and mounting hardware:
python capture.py --port /dev/cu.usbmodemYOUR_PICO --or-wired --mass-g 80

# Higher trigger level if electrical noise or handling triggers too easily:
python capture.py --port /dev/cu.usbmodemYOUR_PICO --or-wired --threshold-g 5

# Acceleration only; omit force:
python capture.py --port /dev/cu.usbmodemYOUR_PICO --mass-g 0

# Recalculate an existing capture with a corrected mass; writes a fresh folder:
python capture.py --reprocess captures/YOUR_RUN/capture.json --mass-g 80

# Test the laptop software without opening any USB device:
python capture.py --demo --or-wired
```

`--window-ms` accepts 4 to 240 ms; `--pre-ms` must be shorter than that window.
Other options: `--timeout`, `--output`, `--supply-v`, `--adc-reference-v`,
`--sensitivity-mv-g`. Run `python capture.py --help` for details.

If it times out, check ST/STBY grounding, rigid axis alignment and the trigger
threshold. If the baseline is invalid, check power/ground and that OUT is on
GP26. Flat/near-zero data alone cannot prove that the sensor is connected.

## Rebuild and verify

The prebuilt file uses Raspberry Pi Pico SDK 2.2.0 with its pinned TinyUSB
submodule and GCC Arm Embedded 12.3.1. The SDK and compiler are external build
dependencies; neither is installed by the capture script.

```sh
export PICO_SDK_PATH=/path/to/pico-sdk
cmake -S firmware -B build -DPICO_BOARD=pico2 -DCMAKE_BUILD_TYPE=Release
cmake --build build -j 4
python -m unittest discover -s tests -v
cc -std=c11 -O2 -Wall -Wextra tests/capture_core_test.c -o /tmp/adxl-core-test
/tmp/adxl-core-test
```

Set `-DPICO_TOOLCHAIN_PATH=/path/to/arm-toolchain` if it is not on PATH. SDK 2.2.0
can fetch/build the official picotool conversion utility for UF2 generation.
The ring-buffer tests exercise trigger timing, pretrigger preservation through
wraps, overwrite rejection and checksums. Laptop tests cover conversion, mass,
range/error flags and USB framing. The included example plot is **synthetic**,
not a measured tap. Physical sensor/Pico timing and force accuracy remain untested.
