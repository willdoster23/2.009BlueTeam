# Brick Mapper on the rooftop Pi

For architecture, lab access, hardware wiring, current behavior, deployment, known issues and project history, read [the full Codex handoff](HANDOFF.md).

Plug a computer into the dedicated inspection switch. Use automatic IPv4 addressing (DHCP) for Ethernet, then open [Brick Mapper](http://brick-mapper.local/) in a browser with WebRTC support. If the name does not resolve, open [the inspection IP](http://192.168.2.4/).

A built-in Ethernet port or any USB Ethernet adapter recognized by the computer will work. The rooftop Pi supplies the address over the wired link. The browser needs no laptop server, adapter selection, or internet connection. Use the same URL and browser profile when you want to see a saved map again.

The dashboard shows live video, microphone playback, encoder positions, winch controls, and the inspection map. **Lens correction** defaults to a slightly reduced version of the earlier 125° perspective projection for live video and new photos. Toggle it beside the video to compare the original view. The lens parameters are an estimate from a reference image. **Tap once** sends one solenoid pulse. **Tap 3 times (2 Hz)** sends three pulses spaced 0.5 seconds apart. **Stop tap** stops the output. Each pulse runs at about 490 Hz, with 50 ms at 210/255 duty and 30 ms at 50/255 duty before going idle.

The top navigation opens **Setup**, **Manual**, and **Automatic inspection** while the camera stays connected. Setup holds tap settings, the baseline recording, the wall reference, and route dimensions. Manual holds X/Y movement, photo capture, tapping, and the audio recording list. Automatic inspection holds route execution, the next-step control, and review. The inspection map and exports appear below the camera on Manual and Automatic inspection. **Stop all** remains beside the camera on every page.

Camera capture modes and the lens-correction checkbox sit below the video on every page. Under Manual, **Record 3 taps + photo** saves the current frame and one three-tap recording at the current position. It appears in the map with its score when a baseline is available, and its WAV also appears under **Audio recordings**. A saved wall reference lets it reuse an existing brick address and skip a previously recorded tap location.

Single-tap tests, three-tap tests, baseline recordings, and audio attached to mapped photos are saved in this browser’s job storage. **Audio recordings** restores the saved WAVs after a refresh or reopening the same site address. **Back up map** includes these recordings. **Start new job** clears them with the rest of that job. The offline preview and each rooftop site address have their own browser storage.

**Raw camera mode** switches between the USB camera’s reported MJPEG frame sizes, turns off lens correction, and reconnects the video. Try 1280 × 1024 and 1280 × 720 at the same pose to compare wall coverage. Pixel dimensions alone do not specify the field of view. The robot needs `v4l2-ctl` from `v4l-utils` to list alternatives. The offline preview shows the layout at sample aspect ratios and uses synthetic imagery.

**Tap strength** adjusts peak PWM from 0 to 255 and peak duration from 10 to 150 ms for the next single tap or inspection burst. Defaults are PWM 210 for 50 ms. **Test one tap** records one tap at the selected settings. The hold lasts 30 ms at PWM 50, or the selected peak if lower. PWM 255 is 100% duty during the peak. The map keeps the chosen PWM and duration with each automatic recording. Record a new reference baseline after changing settings.

**Listen to microphone** plays the robot's USB audio live. **Tap once** records about 1.5 seconds around the impact and adds a player under **Tap recordings**. The clip is saved in this browser’s job storage and can also be exported with **Download WAV**. The robot installer selects the first USB capture device. To change the source on an existing robot, run `sudo bash pi/install-audio.sh YOUR_PI_USER ALSA_CARD_ID`.

**Automatic inspection** uses hardware by default on the outrigger and simulation in the local preview. Each hardware step normally records three taps at 2 Hz into a single 2.5-second WAV, starting with a quarter second of microphone samples before the first tap. The recording uses the decoded microphone stream at its captured gain so recordings can be exported for FFT analysis. **Visual only** skips tapping. The browser saves the photo and any recording to the map, leaving tapped cells unclassified until reviewed. Use **Review last step** or open any cell to play or download its WAV.

**Use hardware** requires both encoders and the roof zero. The rooftop controller sends Start at the beginning of every measured move. Each step lowers by the selected Y distance, checks the settled pulley position, takes a photo, and records the selected burst. **Tap 3 times (2 Hz)** in Manual controls records the same burst without moving the winch.

To score taps, place the robot on a known good brick and click **Record baseline**. The robot taps in place. You can also choose **Use as baseline** on a saved burst or import a 48 kHz mono baseline WAV and its impact CSV under **Files and exports**. Brick Mapper looks for three acoustic onsets and falls back to the recorded 0.25 s lead-in and 2 Hz cadence when it cannot separate them. The supplied FFT and log-Mel difference calculation runs in the browser. The map shows **Similar** in green below 10 and **Dissimilar** in red above 10. Scores of exactly 10 remain unclassified.

Open a capture to download its WAV. Under **Timing and CSV**, download the `Impact` and `Timestamp (s)` columns or optionally adjust the automatically filled times. **Back up map** includes the recordings, their impact times, and the active baseline spectrum.

Brick Mapper saves captures in the current browser profile without opening a file dialog. Use **Start new job** to clear the map, photos, recordings, reviews, and baseline in that browser. **Back up map** exports the photos, step recordings, and reviews as one file, which **Import map** can restore on another computer or browser profile. Open a photo and choose **Download photo** to export a separate PNG.

The map spans the page below the camera and shows a photo in each occupied cell. **Print / save PDF** opens the browser's print dialog with a photo report sorted by position. Choose **Save as PDF** in that dialog. **Download all photos** saves the original PNGs together in one ZIP.

## Preview without the LAN

Keep this `pi-hosted` folder together. On a Mac with Python 3, open `preview/Start Preview.command` and use [the local preview](http://127.0.0.1:8765/). The preview uses a sample masonry view, simulated tap recordings, and the same Y pulse control and route planner as the rooftop dashboard. The lens checkbox is available alongside the sample camera. Its photos and map are stored separately from the rooftop dashboard. Open `preview/Stop Preview.command` when finished. The preview server only listens on this computer.

## X and Y movement

- **X** uses the Nano Every for motor control and quadrature feedback. Its D5 and D6 go to the DRV8871 inputs. The encoder connects directly to the Nano Every. Blue goes to 5V, black to GND, yellow to D2, and green to D3. X uses 1,188 channel-A rising edges per pinion revolution and 75.398 mm of rack travel per turn. The X reference is the Nano's power-on count.
- **Y** uses the Pico 2 to read the AS5048A PWM signal on GP2. The rooftop Pi controls the winch through its own relay HAT and uses the pulley count to stop at the requested distance. Down increases the reported cable payout. Start with 6.283185 inches of cable travel per pulley revolution for a one-inch effective radius.
- The browser's **Axis movement** controls accept relative steps in inches. Photos from hardware mode store measured X and Y positions. A lost encoder or controller connection stops an active measured move. The app requires a new roof zero after pulley feedback is interrupted.

X and Y step fields accept larger distances without a fixed total-distance cap. The dashboard carries out a larger move as consecutive controller jogs, and Stop cancels the remaining jogs. Timed Y uses shorter jogs to keep checking the connection between pulses.

With the mechanism unloaded, check that **Right** goes in the expected physical direction and that X reports increasing counts. If X trips on encoder direction, verify the motor leads and encoder A/B before trying again. Check the rack's travel limits before coupling the drive.

At the top, press **Set Y zero here**. Zero is a reference; manual Y moves can go above it and report negative inches. **Pulley setup** lets you enter cable travel per revolution. In **Setup → Winch pulse settings**, choose Up/Down pulse durations and click **Apply winch settings**. These settings are saved on the roof Pi and apply to short manual jogs and the first pulse of measured moves. Defaults are 400 ms Up and 250 ms Down. The adaptive toggle adjusts later pulses from measured travel; turn it off for fixed pulses. Expand **Correction pulses and settling** to adjust minimum/maximum correction durations and the wait between pulses. The UI supports 10–1500 ms pulses and 50–2000 ms settling. Settings can be saved without changing the Y zero.

Each measured move probes first, observes the settled encoder travel and calculates its next pulse. Up and Down use separate response profiles. Down sends explicit Stop after each measured pulse; manual short jogs send Stop after both directions. A retry with no measurable movement cannot be longer than its preceding pulse. Overshoot is reported without automatically reversing the winch. Timed Y does not learn from its own estimated position. See the handoff for limits and physical-test status.

Under **Plan a route**, center the tapper on the first brick. Enter the number of columns and the center-to-center brick spacing in inches. You can enter vertical spacing directly or calculate it by measuring across a few course intervals. Under **Vertical coverage**, enter a brick count or select **Down to lowest Y** to calculate how many courses fit below the starting brick.

Hardware routes require **Lowest Y**, measured down from roof zero. Allow clearance for the payload and winch overrun. The planner keeps target positions within that limit, and the rooftop controller stops a route jog if the pulley reading passes it. Verify the pulley calibration and allow for slip when setting clearance.

If the pulley encoder is unavailable, **Pulley setup** can enable **Estimate Y using winch time** and set an estimated speed in inches per second. This mode sends bounded relay pulses with explicit Stop commands and estimates travel from their duration. It starts at estimated Y zero after a service restart. Confirm physical position with the camera. Its lower limit and brick addresses depend on that estimate.

The route captures the starting brick, goes down the first column, moves right, then goes up the next column. Use fixed spacing on sections with aligned brick centers. Each stop saves a photo and one continuous recording of three taps at 2 Hz. The map and PDF show the measured position and the difference score when a baseline is available. Use **Record baseline** on a known reference brick to score the recordings.

For a hardware map, center on a recognizable first brick and press **Set first brick here** with both spacings entered. After a controller restart or a change to roof zero, return to that same brick before setting the reference again. One brick tile can hold a center and an edge recording. Select the location before running the route or pressing **Tap current brick**. Position the real tapper at the selected location. A mapped tap reserves that brick and location in this browser before sending the command. A tap with an uncertain outcome stays reserved.

The offline preview uses the sample wall geometry to supply each brick crop and identify the brick at the tap point. **Simulated Y slip** shifts the wall relative to the encoder reading so you can see a route pause before tapping a different brick. On the rig, the address comes from the wall reference and encoders. Use **Mark brick and tap point** on the saved full frame to crop the tile and show the contact point. These marks are per photo and can be updated after the camera angle changes.

### Program the boards once from the rooftop Pi

Connect both boards to the rooftop Pi with USB data cables. Keep motor power disconnected during programming. The board programs live in flash and start at power-up. The rooftop Pi automatically finds each one by its serial messages, so normal operation needs only a browser.

1. Stop `blueteam-network.service` on the rooftop Pi during programming so the controller and the upload tools do not open the same port. Use `ls -l /dev/serial/by-id/` to find the devices. If identity is unclear, connect one board at a time during this setup.
2. With [Arduino CLI](https://arduino.github.io/arduino-cli/latest/installation/) installed on the rooftop Pi, run `arduino-cli core install arduino:megaavr`. From this folder, run `arduino-cli compile --upload --fqbn arduino:megaavr:nona4809 --port "$NANO_PORT" firmware/x_nano_every` with `NANO_PORT` set to the Nano's device path.
3. If the Pico 2 has no MicroPython firmware, hold BOOTSEL while connecting it to the rooftop Pi and copy the [Pico 2 MicroPython UF2](https://micropython.org/download/RPI_PICO2/) to its mounted boot drive. Once it restarts, set `PICO_PORT` to its serial device path. With [mpremote](https://docs.micropython.org/en/latest/reference/mpremote.html) installed, run `mpremote connect "$PICO_PORT" fs cp firmware/y_pico2/main.py :main.py`. Power-cycle the Pico to run the saved program.
4. Run the rooftop installer below, or restart `blueteam-network.service` if the updated dashboard is already installed. Open Brick Mapper and confirm that the Nano Every and Pico 2 appear in **Axis movement**. Test a full power cycle and USB reconnect with the mechanism unloaded.

Click **Shut down both Pis** and confirm before unplugging power. The robot stops tapping and schedules shutdown first. The rooftop controller releases the winch relay and schedules its own shutdown after the robot acknowledges. Wait for both Pis to finish shutting down before disconnecting power. If Brick Mapper cannot confirm the robot's shutdown, it keeps the rooftop Pi online and reports the problem.

## Pi setup

The rooftop Pi uses `192.168.2.4/24` on `eth0` and gives client computers addresses from `192.168.2.100` to `192.168.2.199`. The robot Pi uses `192.168.2.3/24`. Keep the inspection switch separate from the building LAN because the rooftop Pi runs its own DHCP server.

Both Pi installers save a named `brick-mapper-eth0` profile to disk with autoconnect priority above the generated DHCP profile. The rooftop DHCP service also accepts machine-specific reservations in `/etc/brick-mapper/dhcp.d/*.conf`. To reserve the robot's address as a fallback, use `dhcp-host=ROBOT_ETH0_MAC,id:*,192.168.2.3` with its actual Ethernet MAC. The rooftop installer preserves that directory. Reimaging the robot still requires reinstalling its camera and control services.

1. Copy this folder to both Pis. For an existing installation, reuse its controller key. For a fresh pair, generate one key with `umask 077; python3 -c 'import secrets; print(secrets.token_urlsafe(48))' > pi/network.key`. Place the same file at `pi/network.key` on both Pis and keep it private.
2. On the rooftop Pi, install `dnsmasq-base` and `avahi-daemon` if needed. Run `sudo bash pi/install.sh roof YOUR_PI_USER` to configure its Ethernet address and controller. Reboot if the address changed, reconnect at `192.168.2.4`, and run `sudo bash pi/install-roof.sh YOUR_PI_USER`. For an existing rooftop controller with the correct address, the second script updates the dashboard and controller.
3. On the robot Pi 4, connect the USB camera and optional USB microphone. Install `ffmpeg` if needed. Extract the ARM64 Linux `mediamtx` binary from the [MediaMTX release](https://github.com/bluenviron/mediamtx/releases/tag/v1.21.0) into `pi/mediamtx`. Run `sudo bash pi/install-robot.sh YOUR_PI_USER` and reboot to apply its Ethernet address and hardware PWM settings.

### Add shutdown to an existing installation

Copy the updated folder to both Pis. On the robot, run `sudo bash pi/install-poweroff.sh robot YOUR_PI_USER --update`. On the rooftop Pi, run `sudo bash pi/install-poweroff.sh roof YOUR_PI_USER --update`. Refresh Brick Mapper and verify that **Shut down both Pis** is enabled. These commands install the shutdown control and restart the controller services.

## Rooftop winch

The rooftop installation uses four-button control through the WatangTech relay HAT. Use each channel's COM and NO contacts across the corresponding button on the handset. The Pico 2 reads the separate pulley encoder. It has no wire to the handset.

The original two series AAA cells provide 3.0 V nominal. The rooftop Pi 4's 3.3 V supply is available at physical pins 1 and 17, with GND at pin 6 or another ground pin. If substituting an external supply, remove the cells and connect the supply only to the handset's battery positive and negative terminals. Confirm the handset's permitted voltage and measure its transmit load before sharing the Pi's supply. A separate regulated 3.0 V supply or the original AAA pack avoids loading the Pi's rail.

| Button | HAT channel | BCM GPIO |
| --- | --- | --- |
| Stop | 1 | 26 |
| Start | 2 | 19 |
| Up | 3 | 13 |
| Down | 4 | 6 |

The rooftop controller needs Raspberry Pi OS with `python3-gpiozero` and `python3-lgpio`. The standard rooftop setup above enables all four channels. The earlier Up-only wiring on channel 1 uses `--live-winch-up`.

Each new Up or Down command sends a 300 ms Start pulse and waits 200 ms after release before driving a direction. Between measured jogs, the controller waits for a fresh pulley reading after the selected settling interval. **Stop** cancels queued and active moves, then presses the remote's Stop button. The four-button winch controls run on the rooftop Pi and work while the robot Pi is offline. Winch status shows the rooftop Pi's relay commands. The Pico supplies position feedback over USB.

## Solenoid wiring

| Pi or power connection | MOSFET driver connection |
|---|---|
| GPIO18, physical pin 12 | `TRIG/PWM` |
| Ground, physical pin 14 | Signal `GND` |
| Fused regulated 12 V output | `DC+` and `DC−` |
| Solenoid coil | `OUT+` and `OUT−` |

Use a 3.3 V-compatible driver and a flyback diode rated for the coil current. The diode stripe faces `OUT+`. Check the PWM signal with the 12 V side off before powering the coil. The robot installer reserves GPIO18 for hardware PWM and disables onboard analog audio. The USB microphone uses a separate input.
