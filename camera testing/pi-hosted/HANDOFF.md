# Brick Mapper — full Codex handoff

Prepared November 18, 2026, America/New_York. Read this before making changes. This document describes the accumulated implementation, deployment history, hardware context, open issues, and user preferences. Source code wins if a historical README statement conflicts with it.

## 1. Start here

The project is the MIT 2.009 Blue Team masonry inspection robot. A roof/outrigger Raspberry Pi controls a winch and an X gantry. A second Raspberry Pi on the suspended robot supplies camera video, microphone audio, and solenoid tapping. An inspector connects a laptop to the inspection Ethernet LAN and opens a browser. The software runs on the roof Pi; inspection evidence and scoring run/store in the laptop browser.

The latest work is **already deployed**: Setup/Manual/Automatic pages, persistent browser audio, separate photo-plus-three-tap capture, encoder-based X/Y motion, adaptive winch pulses, and UI sliders for winch tuning. The last browser verification showed the sliders loading Up **400 ms**, Down **250 ms**, and adaptation enabled. Those are defaults/last verified values, not a guarantee that the user has not changed the saved settings afterward.

At handoff preparation, fresh SSH audits of **both Pis timed out**. This does not prove either Pi is powered off. The supplied audit JSON explicitly records this failure. Do not present the historical configuration below as a fresh successful hardware audit. The last completed deployment and browser check succeeded before that audit attempt.

Do not confuse these three states:

1. Current local source, including files that had been untracked before this handoff.
2. Last successfully deployed software on the roof/robot Pis.
3. Experimental FFT work in a separate investigation folder, which has **not** replaced production scoring.

### On the original Mac

- Project root: `/Users/bishop/.codex/.chatgpt-projects/g-p-6acedda22474819193601f363368814a`
- Canonical Git checkout: `/Users/bishop/.codex/.chatgpt-projects/g-p-6acedda22474819193601f363368814a/work/2.009BlueTeam`
- Main software: that checkout's `camera testing/pi-hosted/`.
- Repository: https://github.com/willdoster23/2.009BlueTeam
- Working branch used during development: `codex/brick-mapper-pi`.
- Before the handoff publication, GitHub main was `807319e` and was substantially behind the running system. Many important files were untracked locally. A fresh clone of the earlier commit is insufficient.
- Other local trees named `brick-mapper-direct`, `brick-mapper-roof`, `github-upload`, and old laptop-hosted ZIPs are historical, not the canonical current app.
- Project `sources/` is a read-only synced-reference directory. Never edit, rename, move, or delete synced files there. It contained no files during this handoff inventory.

The portable handoff archive includes a full working-source snapshot, original references, audit results, Git state, and the separate scoring investigation. Its `START_HERE.md` explains the layout.

## 2. User preferences and operating expectations

- Bias toward completing authorized changes. The user dislikes repeated permission requests, lengthy testing detours during live debugging, and unnecessary control lockouts.
- Keep communication short and distinguish implemented, deployed, simulator-tested, and physically tested.
- User requests such as "push it" usually refer to the roof Pi in live testing; "push to GitHub" explicitly refers to the repository. State which happened.
- For small live tuning requests, make the requested narrow change and deploy it promptly. Do not start unrelated refactoring or long test campaigns.
- Do not change supplied FFT/scoring logic without explicit agreement. Keep proposed alternatives separate.
- Camera remains prominent across all pages. Map is wide below it, with photos and scores.
- One operator UI. Do not make the operator choose which Pi to command.
- End users should connect Ethernet and open a browser, without code, Python, a laptop server, or adapter-specific setup.
- Normal laptop browsing should continue using building Wi-Fi. Inspection commands and media must use the private Ethernet/bridge LAN. Do not silently switch robot access to MIT Wi-Fi.
- Browser evidence should save without directory-selection prompts. Do not change the user's browser/computer download settings to achieve this.
- Offline preview must have clear Start/Stop scripts. Do not leave test servers or headless browsers running after work.
- X/Y zero is a position reference. User explicitly wants negative Y/Up above zero and no fixed UI total-distance cap. Do not reintroduce those limits.
- The user prioritizes fresh encoder readings over preserving a zero across reconnects. Tell them if re-zeroing is needed; do not spend excessive effort recovering an old zero.
- Tests must not accidentally move the real winch or tap the wall. Most prior verification used simulation or intercepted browser requests. One physical tap was explicitly authorized earlier, but do not treat a status check as a command to actuate hardware.
- The user authorized sharing lab credentials, but automatic approval review rejected publishing plaintext passwords and keys to GitHub. Access material is retained in the local portable bundle, separate from public source.

## 3. Physical and network topology

```text
Inspector laptop (Mac, Windows Panasonic, or another Ethernet-capable computer)
    Ethernet port OR USB Ethernet adapter; DHCP
                   |
            Powered Ethernet switch
             /                 \
 Roof/outrigger Pi 4       Local BrosTrend Wi-Fi bridge
 192.168.2.4                    ))) wireless link (((
   | relay HAT                 Remote BrosTrend bridge
   | USB Nano Every                      |
   | USB Pico 2                    Robot Pi 4
                                    192.168.2.3
                                  /      |       \
                             USB camera USB mic  solenoid MOSFET
```

The roof Pi has a direct wired path to the laptop through the switch. A distant bridge failure alone does not explain loss of the roof Pi. Both bridge units need power; they have historically used 12 V wall power, not PoE. A passive 1-to-2 Ethernet splitter is not a substitute for the powered switch.

### Address and access table

| Device | Inspection address | SSH login | Role |
|---|---|---|---|
| Roof/outrigger Pi | `192.168.2.4/24` on `eth0` | `blueteam2` | Browser server, DHCP/mDNS, winch HAT, Nano X, Pico Y |
| Robot Pi | `192.168.2.3/24` on `eth0` | `blueteam3` | USB video/audio, tap output, robot controller |
| Inspector laptop | DHCP `192.168.2.100–199` | N/A | Browser UI, evidence storage, scoring |
| Original Mac during recent work | `192.168.2.130` on `en6` | Mac account `bishop` | Historical observation, not a required fixed address |

**Lab password:** supplied in the local handoff bundle under `private/pi-access.json`. Pi sudo has required the same password. The Mac's administrator password is not known; do not assume the Pi password works there.

```sh
ssh blueteam2@192.168.2.4
ssh blueteam3@192.168.2.3
```

The old robot was `ks3325`, user `blueteam`; its historical MIT Wi-Fi address was `10.29.153.243`, and earlier Ethernet leases included `.2`. Those are historical recovery clues, **not current default endpoints**. Cards/Pis have been replaced and reimaged during development.

### Operator URLs

- Preferred: `http://brick-mapper.local/`
- Fixed-address fallback: `http://192.168.2.4/`
- Offline preview: `http://127.0.0.1:8765/`
- Direct robot video diagnostic: `http://192.168.2.3:8889/usbtest/`
- Direct robot audio diagnostic: `http://192.168.2.3:8889/usbaudio/`

Use the same URL/browser profile to find the same saved job. `brick-mapper.local`, the numeric IP, and localhost have separate browser storage.

### Stable-address implementation

- `pi/configure-ethernet.sh` creates a persistent NetworkManager profile named `brick-mapper-eth0` in `/etc/NetworkManager/system-connections/`, with autoconnect priority 100.
- Both Pi Ethernet addresses are manual/static. No Ethernet default gateway or DNS is advertised/configured for general internet use. `ipv4.never-default=yes`, `ignore-auto-dns=yes`.
- Roof DHCP service provides `.100–.199`, /24, 12-hour leases, and no default-router/DNS options, so laptop Wi-Fi remains its internet route.
- DHCP config: `/etc/brick-mapper/dnsmasq.conf`; optional reservations: `/etc/brick-mapper/dhcp.d/*.conf`; leases: `/var/lib/misc/brick-mapper.leases`.
- Avahi publishes `brick-mapper.local` on roof `eth0`; OS hostname may still be `blueteam2`.
- Laptop Ethernet should use automatic addressing. No custom code was written to the USB Ethernet adapter itself. Built-in Ethernet also works.
- The old Mac Internet Sharing/`bridge100` setup was superseded by the roof-hosted DHCP LAN. Do not re-enable sharing as the normal solution.
- Bridge management addresses were historically `.1`/`.2`; exact current bridge model/configuration is not freshly verified. ARP entries sharing a bridge MAC are not alone proof of an IP collision.

### Network troubleshooting without changing settings

On the original Mac:

```sh
route -n get 192.168.2.4
ifconfig en6
arp -an
curl --connect-timeout 3 http://192.168.2.4/api/app
curl --connect-timeout 3 http://192.168.2.4/api/network
```

Check the actual interface first; `en6` is only the last observed adapter. If the route goes through MIT Wi-Fi/default gateway, do not use that route for inspection commands. Other operating systems/interface names differ.

A recent outage had Ethernet carrier and the expected laptop IP, but roof SSH/web failed and the route showed a failed-host cache. Unplugging/reconnecting only the laptop Ethernet cable restored access without changing Pi IPs. The exact underlying fault was not conclusively diagnosed. Do not claim that all network failures are DHCP/address changes.

## 4. Software architecture and file map

Everything below is relative to `camera testing/pi-hosted/`.

| File | Purpose |
|---|---|
| `app/map.html` | Actual shared UI, CSS, map/gallery/export/review logic, controls markup |
| `app/pages.js` | Setup/Manual/Automatic hash navigation and shared stop behavior |
| `app/dashboard.js` | Polling, operator controls, X/Y requests, tuning UI, tap/audio list, live listening, shutdown |
| `app/player.js` | WebRTC camera, retry, lens renderer, capture, raw-mode selection, synthetic preview wall |
| `app/storage.js` | IndexedDB photos/mapping/recordings, backups, imports, exports, audio players |
| `app/automatic.js` | Stationary photo+burst, route execution, reservations, saving/review |
| `app/tap-score.js` | Browser implementation of supplied signal-processing algorithm |
| `app/tap-analysis.js` | Baseline, threshold 10, scoring UI, timing/CSV controls |
| `app/server.py` | Roof HTTP server, token checks, static app files, media proxy, command dispatch |
| `app/network.py` | Fixed Pi roles/addresses, Ethernet-bound backend requests, polling, shutdown coordination |
| `app/index.html` | Historical companion file; roof server actually renders `map.html` |
| `pi/agent.py` | Authenticated controller service, role identity, commands/telemetry, shutdown |
| `pi/outrigger.py` | USB role discovery, Nano bridge, Pico Y position/zero, measured/adaptive winch controller |
| `pi/winch.py` | Active-low HAT outputs, Start/pulse/Stop scheduling and cancellation |
| `pi/x_axis.py` | X conversion/interface and older direct-GPIO fallback |
| `pi/x-axis.json` | Null direct-GPIO pin placeholders; not the current USB Nano wiring configuration |
| `pi/tap.py` | Robot GPIO18 hardware PWM, peak/hold, single/three-tap commands |
| `pi/tap_audio.py` | Record microphone PCM around locally triggered taps and return WAV |
| `pi/camera.py` | USB camera modes, FFmpeg publishing, service/control state |
| `pi/configure-ethernet.sh` | Persistent fixed Pi Ethernet profiles |
| `pi/install.sh` | Base role/network/controller installation |
| `pi/install-roof.sh` | Roof app, DHCP, Avahi, USB/outrigger modules and services |
| `pi/install-robot.sh` | Robot controller/PWM, camera, MediaMTX and fixed Ethernet |
| `pi/install-audio.sh` | USB ALSA card selection and audio publishing service |
| `pi/install-poweroff.sh` | Poweroff timer, constrained polkit rule, optional existing-install updates |
| `firmware/x_nano_every/x_nano_every.ino` | Arduino Nano Every X motor/encoder firmware |
| `firmware/y_pico2/main.py` | Pico 2 PWM encoder acquisition and USB serial reports |
| `preview/serve.py` | Local simulator using the same app/server/controller code with fake I/O |
| `preview/Start Preview.command`, `Stop Preview.command` | Explicit Mac preview lifecycle |
| `tests/test_winch_pulses.py` | No-hardware pulse scheduling, response-model and saved-tuning tests |

### Installed filesystem

| Roof Pi | Purpose |
|---|---|
| `/opt/brick-mapper/app/` | Served UI and roof web backend |
| `/opt/blueteam-network/` | `agent.py`, `outrigger.py`, `winch.py`, `x_axis.py`, shared key |
| `/etc/brick-mapper/outrigger` | Marker enabling USB outrigger mode |
| `/var/lib/brick-mapper-outrigger/pulley.json` | Pulley calibration, mode, and saved winch tuning |
| `/var/lib/brick-mapper-outrigger/pulley-origin.json` | Encoder epoch/tag and reference count |
| `/etc/brick-mapper/x-axis.json` | Direct-GPIO fallback settings |
| `/var/backups/brick-mapper/` | Timestamped software rollback copies |

| Robot Pi | Purpose |
|---|---|
| `/opt/blueteam-network/` | `agent.py`, `tap.py`, `tap_audio.py`, `camera.py`, shared key |
| `/usr/local/bin/mediamtx` | ARM64 MediaMTX binary |
| `/etc/mediamtx/mediamtx.yml` | Video/audio paths and LAN-only WebRTC endpoints |
| `/run/brick-camera/` | Runtime camera mode/control state |
| `/boot/firmware/config.txt` | PWM overlay / analog-audio configuration from installer |

The application has no npm build step. It is Python standard-library server code plus directly served browser JavaScript. GPIO needs the Pi packages below. Firmware tools are separate.

## 5. Services and APIs

### Roof services

- `brick-mapper.socket`: listens at `192.168.2.4:80`, bound to `eth0`, socket activation.
- `brick-mapper.service`: Python roof web server, runs as the Pi user, restarts on failure.
- `blueteam-network.service`: controller on `192.168.2.4:8091`, `--role roof --live-winch`, supplementary `dialout` group through outrigger drop-in.
- `brick-mapper-dhcp.service`: DHCP-only dnsmasq.
- `avahi-daemon.service`: `.local` publication.
- `brick-mapper-poweroff.timer`/`.service`: scheduled shutdown, normally inactive until requested.

### Robot services

- `blueteam-network.service`: `--role robot --tap`, at `192.168.2.3:8091`; startup/stop PWM setup/reset.
- `mediamtx.service`.
- `bridge-usb-camera.service`.
- `bridge-usb-audio.service`.
- Shutdown timer/service as above.

### Endpoints

| Roof browser endpoint | Purpose |
|---|---|
| `GET /api/app` | Identifies app and `hostedOn: roof` or `preview` |
| `GET /api/network` | `roof` and `robot` reachability/data, fixed role addresses |
| `GET /api/health` | Robot media HTTP availability and response time |
| `POST /api/winch` | Remote Start/Stop/Up/Down jog |
| `POST /api/x-axis` | X move/stop/renew |
| `POST /api/y-axis` | Y up/down/zero/stop/renew/calibrate/**tune** |
| `POST /api/tap` | Single/burst/stop, optional recording, peak PWM/duration |
| `POST /api/camera` | Raw capture-mode selection |
| `POST /api/poweroff` | Coordinated shutdown |
| `/media/video/whep`, `/media/audio/whep` | Same-origin WebRTC signaling proxy and session deletion |

Controller APIs are on each Pi's `8091`. They use `X-Network-Key` from the shared `/opt/blueteam-network/network.key`. Do not put this key in browser JS. Roof-to-robot HTTP connections bind to the roof Ethernet source address. The controller protocol exchanges role/boot identity, sample positions, sequences and acknowledgements; the roof is authoritative for X/Y.

Browser command requests require the current `X-Photo-Token`, expected Origin and Host. Allowed roof hosts are `brick-mapper.local` and `192.168.2.4` (port 80 variants). The web token is generated anew each server process.

**Refresh open browser tabs after restarting `brick-mapper.service`.** Their old control tokens can become invalid even if the already-connected camera is still displaying frames. A different hostname not on the allowlist may also fail requests.

Commands carry unique IDs. Do not automatically replay a movement or tap after an uncertain acknowledgement. Y/X renewals are leases, not repeat-move requests. Read the existing protocol before adding retries.

## 6. UI and operator workflow

### Setup

- Hardware/simulation operation mode.
- Tap PWM and peak-duration sliders, test tap, default reset.
- **Winch pulse settings**: separate Up/Down first/manual-jog pulse sliders; adaptive-feedback toggle; expandable minimum/maximum correction pulses and settling times; **Apply winch settings**.
- Baseline recording/import.
- Set Y zero, set first brick, pulley calibration and optional timed Y fallback.
- Route dimensions, center spacing, lowest Y, and sample-based spacing calculation.
- Connection diagnostics.

### Manual

- X Left/Right by entered inches, Stop X.
- Y Up/Down by entered inches, Stop Y and nearby **Set Y zero here**.
- Tap once, three taps, **Record 3 taps + photo** without moving X/Y.
- Photo capture, visible audio recordings, download/playback.
- Expandable short winch jogs and relay status.
- Full-width map/gallery and exports below the camera.

### Automatic inspection

- Single next step and serpentine route execution.
- Each ordinary step moves, stops, captures a photo, records three taps, calculates a score if possible, and saves evidence.
- Review is optional; no forced green/red/yellow label before running the next step.
- Route goes down one column, moves X, goes up the next, and repeats.
- Shared camera, audio listening and stop controls remain available across pages.

### Captures and exports

- PNG photo per capture, unique generated name.
- Attached three-tap WAV and analysis metadata.
- Standalone single, burst and baseline audio also persisted in the job.
- `Print / save PDF` invokes browser print with the map/photo report.
- Individual photo/WAV downloads.
- All-photo ZIP export.
- JSON **Back up map** includes photos, audio, reviews, impact times, mapping data and baseline, and can be imported.
- **Start new job** clears the current browser job's photos, mapping, recordings, review state and baseline. It is not a command to erase a Pi filesystem.

## 7. Evidence storage and the stuck-tapping fix

The roof Pi hosts software and relays responses; it is not the permanent inspection photo/audio archive. Video is live. Tap WAVs are generated in robot memory and returned over the LAN. Browser captures/recordings live in IndexedDB on the inspector computer.

- Database: `blue-team-brick-mapper`, schema version 3.
- Stores: `photos`, `mapping`, `recordings`.
- Baseline/job identity also use localStorage, including `brick-mapper-tap-baseline` and `brick-mapper-job`.
- Same browser profile AND same origin are needed to reopen the same job.
- Clearing browser site data, using private browsing or another profile/origin can make evidence unavailable. Export a backup for transfer.
- Browser auto-storage does not mean PNG/WAV files silently appear on Desktop. Downloads use the browser's existing download behavior. No computer settings were changed.

Earlier single/burst test recordings existed only in the live tab and were lost after closing/reloading unless downloaded. This was corrected: single, burst, baseline and mapped audio now save persistently and restore into the audio list. A real windy clip from that day is included in the private/portable references.

The later "Wait for the current operation to finish" issue had a reproduced frontend defect: `tapBusy` stayed true while awaiting browser storage, even after the robot finished and returned the WAV. A simulated slow save reproduced disabled tapping with the robot idle.

Deployed fix:

- Release the hardware tapping state when the recording response returns, independently of saving.
- Do not let an older save completion clear a newer tap's busy state.
- Bound IndexedDB open/write waits to eight seconds per stage; abort timed-out transactions and close late completed opens.
- Keep failed recordings playable/downloadable in the open tab, show a saving error, and provide **Retry saving audio**.
- Track outstanding recording saves separately so starting a new job cannot silently clear an in-flight save.

**The exact cause of the user's actual browser-storage delay was not proven.** Other tabs, queued storage work or storage/browser behavior were possibilities, not established diagnoses. Do not claim a browser engine bug or SD-card write overload was conclusively found.

Another UI defect hid stationary-capture errors on a different page. That was fixed by adding visible Manual feedback and a missing-position preflight that leaves X and standalone tapping available.

## 8. Camera and lens correction

- Camera referred to by the user as ELP; earlier recorded model context was `ELP-USBFHD01M-L180`, OV2710-class 1080p USB fisheye. Treat that exact SKU as historical discussion, not a fresh label inspection.
- An earlier USB listing identified `Innomaker-U20CAM-1080p-S1`; USB product names and purchased lens/camera description may differ. Check the actual label if precise commercial specifications are needed.
- USB `/dev/video0` capture, MJPEG supported. `/dev/video1` may be a secondary/metadata node, not another camera.
- Recent reported modes: `1920x1080`, `1280x720`, `1024x768`, `640x480`, `800x600`, `1280x1024`, `320x240`; actual mode list comes from the connected camera.
- Default/currently used stream has commonly been `1280x720` at 30 fps.
- Raw mode dropdown is under the camera on every page. A switch disables lens correction and reconnects video.
- Resolution/aspect ratio alone does not establish the widest optical field of view. Sensor cropping/scaling may differ across modes; compare at the same physical camera pose.
- Current browser correction uses WebGL and an approximate equidistant-to-perspective fit from a reference frame; it is not a full calibrated camera model.
- Renderer constants include a 125-degree perspective output, 0.92 correction mix, and the fitted focal/center terms in `player.js`.
- The user asked for less correction because the earlier view overcorrected. The checkbox allows raw/corrected comparison.
- Camera mount is adjustable. Do not assume a fixed tap-head pixel position across sessions.
- PNG captures follow the displayed correction choice. Browser display shaping/correction should be distinguished from camera sensor capture modes.
- The offline preview has a correction checkbox but deliberately does not simulate bending its sample wall.

MediaMTX uses RTSP internally at `127.0.0.1:8554` and WebRTC at robot TCP `8889` / UDP `8189`. Paths are `usbtest` and `usbaudio`. FFmpeg publishes the USB streams. The old `rpiCamera`/CSI configuration failed because this is a USB camera; do not restore it.

## 9. Microphone, tap waveform and timing

The current microphone is a 3.5 mm microphone connected through a USB audio adapter, not the earlier microphone arrangement. Its precise microphone/adapter model is not recorded reliably in this chat. The audio installer picks a USB ALSA capture card or accepts an explicit ALSA card ID.

- Audio publisher captures 48 kHz and sends Opus at 64 kbit/s with low-delay application mode to MediaMTX `usbaudio`.
- `tap_audio.py` decodes that live stream to 48 kHz mono 16-bit PCM and returns WAV.
- Single tap recording: about 0.25 seconds before the software trigger and 1.25 seconds after, approximately 1.5 seconds total.
- Three-tap burst: 2 Hz, three impacts, approximately 2.5 seconds continuous audio, not three separate recordings.
- Nominal impact times are 0.25, 0.75 and 1.25 seconds within that WAV.
- Actual wall contact is not guaranteed to equal the command instant: solenoid travel, USB/Opus buffering, microphone path and mechanical contact add uncertainty.
- **Single-tap WAVs are digitally boosted** up to 16x toward a peak of 26000 for playback. **Three-tap burst WAVs are not normalized.** Do not compare playback loudness and assume the same acquisition gain.
- `Listen to microphone` is live playback. Saved clips have separate players. Playing a saved clip pauses other audio elements so the user can hear it.
- Current gain calibration feature is **not implemented**. Discussion recommended checking ALSA Capture/Mic/Mic Boost/AGC controls, measuring unnormalized burst peaks/background/clipping, choosing stable gain, and taking a new baseline after gain changes.
- Lower gain can reduce input clipping but generally lowers both tap and wind. It does not selectively remove wind, and post-recording attenuation cannot undo clipping.
- Gain/AGC changes affect the production absolute-level score. Keep acquisition settings fixed during comparisons.
- Robot wall-clock timestamps were observed to be wrong by roughly days during some LAN-only sessions. Use local monotonic/sample-relative timing for recording/motion, not cross-device wall-clock differences to infer impact timing.

## 10. Solenoid wiring and pulse generation

Robot Pi owns the solenoid. Roof Pi does not generate its PWM.

| Connection | Destination |
|---|---|
| Robot BCM GPIO18, physical pin 12 | MOSFET `TRIG/PWM` |
| Robot GND, e.g. physical pin 14 | MOSFET signal GND/common ground |
| Fused regulated 12 V +/− | MOSFET power `DC+`/`DC−` |
| Solenoid coil | MOSFET `OUT+`/`OUT−` |

The cited MOSFET board was Amazon `B0FMJH3DML`; an earlier board reference was `B0DZP27C2N`. The coil was specified by the user as **12 V, 2.5 A**. Trigger interface must be 3.3 V-compatible. A flyback diode belongs across the inductive load, stripe toward positive. Do not power the coil from GPIO or the Pi 5 V rail.

Production defaults mirror the provided Arduino peak/hold sketch:

- Hardware PWM about 490 Hz.
- Peak: PWM 210/255 for 50 ms.
- Hold: PWM 50/255 for 30 ms, capped by a lower selected peak.
- Off after 80 ms total at the defaults.
- Burst spacing: 500 ms start-to-start (2 Hz), exactly three taps.
- UI peak slider: 0–255; peak duration: 10–150 ms.
- PWM 255 means 100% during the peak, not increased supply voltage.

GPIO18 hardware PWM is reserved by the installer; onboard analog audio is disabled. USB microphone remains separate. The user explored increasing peak duration to hit harder; do not reinterpret the 210 peak as full duty or silently alter timing logic.

## 11. Winch HAT, remote and current tuning UI

The winch itself has no integrated encoder in this setup. Its remote buttons are connected through roof Pi relay dry contacts. The separate pulley encoder supplies Y position.

| Remote button | Relay HAT channel | Roof BCM GPIO |
|---|---|---|
| Stop | 1 | 26 |
| Start | 2 | 19 |
| Up | 3 | 13 |
| Down | 4 | 6 |

HAT outputs are active-low. COM/NO contacts bridge each remote pushbutton's actual switched contacts. Do not apply the Pi's voltage across a switch signal just because it is a relay contact. Relay clicks alone do not prove the remote switch wiring or radio command worked.

User identified the remote as Craftsman Spirit `YTK-V2S`, label `K45775254`. They soldered the remote buttons incrementally. Earlier tiny/glued switch pads were fragile. The current claim that Up/Down work is based on user observations, not a newly inspected remote schematic.

Remote originally used two AAA cells. User removed the cells and connected remote power to the **roof Pi's 3.3 V/GND**, not the Pico. That power rail is continuously present while powered, not software-switched with button presses. Exact permitted remote voltage was not independently proven. Keep this hardware history clear; do not accidentally assign remote power/control to the Pico.

### Timing and learning

The winch is on/off. There is no analog speed/voltage control. Pulse duration and spacing are the available controls.

- Start command: 300 ms, then 200 ms gap before direction.
- Each measured move probes first and measures settled encoder travel.
- Adaptive estimate is travel per actual relay-on millisecond, including the empirical effect of the observed move/settling. It is an approximate pulse-response model, not a validated physical model.
- Up and Down use separate profiles and different remaining-distance fractions.
- Faster observed movement updates the estimate immediately; a slower/slipping sample reduces it gradually, limiting sudden increases.
- No measurable progress does not cause an increasing blind retry. Two no-progress pulses end the move with a reported error.
- Encoder monitoring can release a pulse early near the target. It does not guarantee zero overshoot.
- Y measured moves accept a **0.5 inch target tolerance** for both Up and Down. Pulses release when they reach the window; settled positions up to 0.5 inch short or past the target finish without another correction or an overshoot warning. Larger overshoots still stop with a warning. Requests of 0.5 inch or less already fall within the window and complete without arming or issuing a direction pulse; a busy relay controller still rejects them. Installed on the roof Pi with calibration, zero and pulse tuning preserved; backup `/var/backups/brick-mapper/20261119T053351Z-y-half-inch/`. Fifteen automated tests pass; no motor-driven test was run.
- Down sends explicit Stop after every measured pulse even if legacy `holdToRunDown` is true.
- Up sends explicit Stop when required by its configured handset behavior.
- Manual short jogs now use the chosen Up/Down UI pulse and send explicit Stop afterward.
- The response estimate resets per measured move. It is not a learned permanent calibration.
- Timed Y does not learn from its own synthetic feedback.

### Defaults in current source

| Setting | Up | Down |
|---|---:|---:|
| First/manual pulse | 400 ms | 250 ms |
| Minimum correction | 80 ms | 40 ms |
| Maximum correction | 600 ms | 250 ms |
| Settling interval | 250 ms | 700 ms |
| Remaining-distance fraction | 0.90 | 0.45 |
| Maximum growth after valid progress | 1.75x | 1.15x |

The user repeatedly found short probes insufficient: 150/60 ms, then 250/150 ms were tried before 400/250 ms defaults. They asked for sliders to avoid redeployment for every adjustment. Short corrective pulses can also fail to overcome a mechanism/remote deadband. Raise **Minimum correction pulse** or disable adaptation rather than assuming only the probe matters.

### Setup → Winch pulse settings

- Up and Down pulse sliders, 10–1500 ms.
- Adaptive toggle; off repeats the selected pulse, with target monitoring still active.
- Expandable minimum/maximum correction pulse controls, 10–1500 ms.
- Per-direction wait/settle, 50–2000 ms.
- **Apply winch settings** saves to the roof Pi, applies to subsequent moves and short jogs, and does not move the winch.
- UI keeps minimum ≤ initial pulse ≤ maximum by adjusting related sliders.
- Settings are stored in `pulley.json` under `winchTuning`, returned in `yAxis.winchTuning`, and preserved by pulley calibration updates.
- API: `POST /api/y-axis` with `{id, action:"tune", winchTuning:{adaptive,up:{pulseMs,minPulseMs,maxPulseMs,settleMs},down:{...}}}`.
- Saving tuning does not require a valid Y zero or a connected Pico. It still requires an available idle controller.

The last browser check verified loading defaults and the Apply control. Unit tests verified persistence across a controller reconstruction, preservation of zero/calibration, and no motor callbacks while saving. There was **no physical winch test of the new sliders** performed by the assistant.

### Remaining motion constraints

- No fixed total-distance cap in the UI. Large requests are split into internal jogs (X 6 in, Y 12 in; timed Y further limited per chunk).
- Negative measured Y is supported for manual moves above zero.
- Automatic route planning still assumes a roof-relative wall span and a lowest Y; do not assume every negative-origin automatic route case is implemented.
- Y motion lease: 2 seconds, refreshed from the browser; overall measured-move timeout: 90 seconds.
- Feedback freshness is required. The controller lives on the roof Pi next to the USB encoders; Y feedback does not traverse the distant Wi-Fi bridge. The remote's radio/mechanical response still introduces delay.
- The pulley can slip. Encoder-derived distance and camera/brick identity are not ground-truth guarantees.
- Ground detection is not implemented. More conservative downward pulses cannot guarantee clearance if the requested travel/zero/route limit is wrong.

## 12. X gantry, motor driver and Nano Every

The installed architecture uses an **Arduino Nano Every over USB** to the roof Pi. It is not the classic ATmega328P Nano and not the early direct-Pi GPIO proposal.

| Nano Every | Connection |
|---|---|
| D5 | Motor driver IN1 |
| D6 | Motor driver IN2; user referred to this as the orange wire |
| D2 | Encoder channel A (yellow in prior wiring) |
| D3 | Encoder channel B (green in prior wiring) |
| 5 V | Encoder positive, previously blue |
| GND | Encoder ground, previously black; common logic/driver ground |
| USB | Roof Pi |

The driver was identified in development as DRV8871. Motor power and driver output carry motor current; a Nano GPIO cannot replace the motor driver. Verify the actual board's terminal labels before moving wires.

Purchased references: motor Amazon `B0G4C4KKTB`, driver `B07K9N85LK`; McMaster rack/pinion `2664N377` and `2485N222`. These are component references, not freshly revalidated listings.

Conversion in current code:

- Pinion/rack travel: about 75.398 mm per revolution.
- 1188 channel-A rising edges per pinion revolution.
- `MM_PER_COUNT` about 0.0634665 mm (user's earlier value 0.06347).
- This is A-rising counting with B determining direction, not a 4x quadrature count. Changing edge mode requires changing calibration.
- Roof `X_RIGHT_ENCODER_SIGN = -1`: the installed rightward direction decreases raw Nano counts. This reversal was intentional.
- X origin is normally the Nano's power-on encoder count. `REFERENCE` supports restoring an origin during an in-place firmware update; it is not an automatic absolute homing sensor.

Firmware uses PWM drive with startup/approach/cruise/boost behavior. Current source values: approach 108, cruise 210, startup 220, boost 255; startup interval 150 ms, boost after 450 ms without progress, stall 700 ms, move timeout 12 s. Host wait is about 15 s. Nano link timeout is 450 ms. The hardware previously stalled/whined on the rack; more aggressive firmware helped some jogs but did not prove every mechanical stall solved.

Arduino upload FQBN: `arduino:megaavr:nona4809`. Stop the roof controller before programming so two processes do not compete for serial. Firmware is stored on the Nano and starts at power-up.

## 13. Y encoder and Pico 2

- Separate pulley encoder, read by a Raspberry Pi Pico 2 over USB to the roof Pi.
- Firmware expects AS5048A PWM on **GP2**, physical Pico pin 4.
- Common ground is necessary. Do not invent unverified wire colors or feed a 5 V signal into a 3.3 V input; exact current power/level-shifting wiring was not freshly inspected for this handoff.
- Pico firmware uses PIO at 8 MHz to measure the PWM frame and unwrap angle.
- Reports accumulated 12-bit position: **4096 counts per revolution**.
- Calibration starts at effective pulley radius 1 inch → circumference **6.283185 in/rev**; user planned to refine it.
- `direction=1` was established as increasing payout/down in prior tests. User asked to remove routine direction-selection UI; do not reverse it casually.
- The updated Y opposite-direction stop requires **1024 counts (one quarter revolution / 90 degrees)** of net reverse travel from the measured move's starting position, regardless of jog distance or pulley travel calibration. Smaller reverse readings do not trigger this error. Feedback-loss, no-progress, timeout, and route-limit checks remain in place. Eleven local tests pass, including recovery from sub-quarter-turn reverse readings and stopping/disarming at or beyond a quarter turn in both directions and with both encoder polarities. Installed on the roof Pi after Ethernet was reconnected; the controller restarted successfully with pulley calibration and tuning unchanged. Backup: `/var/backups/brick-mapper/20261119T050251Z-y-quarter-turn/`. The Y encoder showed no valid signal at installation; no motor-driven test was run.
- Pico program lives as flash `main.py` and should run at power-up. Existing local source is included; flash contents were not read back in this handoff.

### Serial protocol and auto-discovery

The roof opens both USB serial devices with a nominal 115200 baud setting. The
Nano reports at roughly 50 ms intervals. The updated Pico source reports at
10 ms intervals (100 Hz), drains its PIO FIFO every 250 microseconds, and still
unwraps the encoder's approximately 1 kHz PWM frames. The roof serial worker
checks the devices every 5 ms. Pico USB CDC throughput is not increased by
changing the nominal UART baud setting; do not change the Nano's baud to try
to speed up the Pico.

On November 19, the Pico's flash `main.py` was read back and matched the prior
20 Hz source. With the user confirming the winch was unplugged, the updated
Pico firmware and roof reader were installed and verified. USB output measured
100.0 Hz (195 reports in about two seconds, one stable epoch). A separate
250 ms hardware capture produced 273 valid PWM frames, approximately 1092 Hz;
15 short/out-of-range frames were rejected by the existing frame filter. This
does not establish the cause of the user's incorrect final counts after fast
rotation. Physical slow/fast one-revolution accuracy still needs checking.
The firmware restart invalidates the previous Y zero. Backups on the roof:
`/var/backups/brick-mapper/20261119T024251Z-y-100hz/`.
Y status also exposes `encoderSequence`, `encoderEpoch`, and `encoderAgeMs`
for passive diagnosis of report rate, counter restarts, and stale readings.
After deployment, the user made an approximately one-revolution quick turn;
raw counts changed from approximately zero to 4371. The user confirmed this was
not a precisely marked full revolution. Do not use that trial to recalibrate
4096 counts/revolution or declare a 6.7% encoder error. The Pico epoch remained
unchanged from the firmware restart through the test. A subsequent roof API
check measured 100.3 reports/s, maximum sample age 10.4 ms, Y connected and
unzeroed, X connected, and no busy relay output. No motor-driven test was run.

```text
BM1 X <sequence> <signed-counts> <state> <move-tag>
BM1 Y <sequence> <signed-counts> <idle-or-signal> <epoch-tag>
```

The roof scans `ttyACM*` and `/dev/serial/by-id/` and identifies role by messages. Do not hardcode `ttyACM0` as Nano or `ttyACM1` as Pico: reconnects changed them. Last recovery saw Nano at `ttyACM2`, Pico at `ttyACM0`.

X commands: `PING`, `MOVE <token> <counts>`, `STOP`, optional `REFERENCE <count>`. Pico reports only encoder information; it does not control the winch or remote.

The origin is tied to the Pico epoch/tag. Reconnecting/restarting the Pico can invalidate zero even while readings resume. The user accepts re-zeroing and prioritizes live encoder response.

### Recent Pico recovery

After a roof controller restart, X resumed but Y reported no fresh readings. Pico was enumerated as a USB MicroPython board. A competing-reader check and then a stopped-controller read got no serial output. A serial soft restart did not restore output. Unplugging/reconnecting only the Pico's USB cable restored live counts. The root cause was not conclusively determined. A check found `throttled=0x0` on the roof Pi; this is not proof of all power wiring being correct forever.

Do not claim this was definitively an encoder wiring fault, definitely caused by the new controller, or definitely fixed permanently. Inspect current serial identity/messages if it recurs. A PWM `signal` state differs from no USB serial output.

## 14. Brick map and real versus simulated identity

The goal is an inspector-readable map with a photo and tap similarity result at a physical wall location, including staggered masonry and center/edge retests.

- Coordinates come from measured X and Y inches/counts when available.
- Stored metadata includes counts, inches, source, whether Y was zeroed, tap PWM/duration, audio, analysis, status, and optional brick/zone/crop/tap-point.
- In the preview, synthetic wall geometry directly knows the brick under the tapper, including alternating half-brick staggering and a simulated Y-slip offset.
- In real hardware mode, the mapped brick address is inferred from the wall reference and center spacings. It is **not** reliable visual recognition of a unique real brick.
- The live reference uses starting X/Y, spacing, roof boot identity, Y-zero reference and a wall ID. Reboot/re-zero can require returning to the same recognizable first brick and resetting the reference.
- One brick can have center and edge samples and multiple captures. The UI groups them into a brick tile rather than assuming every X/Y grid intersection is a unique brick.
- A tap reserves a brick/zone before commanding it; duplicate or uncertain outcomes remain held to reduce accidental duplicate taps. Do not silently clear reservations after lost acknowledgements.
- Measured target/address checks currently use tolerances around grid centers (roughly 0.37 X cell / 0.29 Y cell in one path). These are heuristics, not vision-derived mortar boundaries.
- Manual marking of the tap point and a brick crop is available on saved photos. Marks are per photo, useful after camera mount movement.
- The tapper looked bronze/brass-colored and near the bottom of the real reference frame. That can inform future color/shape detection, but no robust deployed head tracker was verified.
- Canny edges, mortar segmentation, perspective rectification, optical flow, fiducials and image mosaicing were discussed, not all implemented.
- Repeated homogeneous bricks and encoder slip make globally unique visual brick identity difficult. Do not promise the current map identifies each brick uniquely from camera pixels alone.
- Different columns may hit the same brick in a staggered wall. Empty cells are not necessarily missed inspection; grouping by actual brick/zone is the intended representation.

The original `gallery.html`, UI specification PDF and real frame `download.png` are included in the portable references. Treat them as reference material rather than instructions that override the user's current request.

## 15. Supplied FFT algorithm and separate investigation

Production logic was intentionally kept compatible with `/Users/bishop/Desktop/Tap Sorting Script.py`. The portable archive contains the original script and a separate investigation. Production `app/tap-score.js` SHA-256 has remained:

```text
72540aff4cfcef1a6d950cfc16136c0a8c3c0620887cbbbbf42a1b428fc45f14
```

Pipeline:

1. Decode mono 48 kHz WAV.
2. Try to detect three onsets using 2 ms derivative-energy frames, a background-based threshold, and approximately 500 ms cadence.
3. If detection fails on a sufficiently long nonsilent recording, fall back to nominal 0.25/0.75/1.25 s times.
4. Select 450 ms following each time.
5. Subtract the selected slice mean, apply a Hann window over the **whole 450 ms**, then compute an FFT with `n=1024`.
6. Because the selected slice is much longer than 1024 samples, only its first **21.333 ms** reaches that FFT. Most of the selected ringing is discarded, while the beginning has very small full-window Hann weights.
7. Compute each tap FFT separately, average magnitudes, square, apply 64 Mel filters, take log power and floor to an 80 dB range.
8. Compare the mean absolute difference across the 64 log-Mel values against the baseline.

User threshold: difference **<10 green/similar**, **>10 red/dissimilar**, **exactly 10 unclassified**. This is not a calibrated probability of brick damage.

The three 21 ms snippets are not concatenated into one FFT. The entire ~2.5 s recording is saved and used for timing detection; it is not all used in the final spectra.

### Established investigation results

- Browser behavior was cross-checked against original Python; selected results agreed within approximately 0.0001.
- The FFT/window mismatch exists in the supplied original script, not just the browser port.
- The inspected windy WAV had 7.82% samples at the digital rails, but the user correctly noted it was wind-dominated. Do not infer how much clipping causes clean repeated-hit variation from that one recording.
- Timing shifts and amplitude changes can substantially alter the current score. Half amplitude shifted one score by 6.0206; quarter amplitude by 12.0412, enough to cross the threshold on otherwise identical audio.
- Clean synthetic, unclipped signals showed sensitivity too. A full-window alternative captured later ringing that the production FFT missed, but was not uniformly more robust to all timing shifts.
- In-memory full-length FFT and Welch-style experiments were performed separately and not deployed.
- The quiet/noise-only fallback can produce an apparent score without three trustworthy detected impacts. This is a known quality problem, not solved by the UI's green/red simplification.

Recommended future work, still pending explicit algorithm-change agreement:

- Analyze the complete recording to locate actual impacts, distinguish tap contact from coil/return clicks, and reject uncertain events rather than silently trusting a scheduled time.
- Test pre-impact padding and meaningful post-impact ringing windows on clean real repeated hits.
- Make window length and FFT length consistent.
- Validate gain normalization/filtering instead of assuming it preserves diagnostic information.
- Collect repeated recordings from the same spot with fixed settings, then compare against genuinely different bricks and revalidate the threshold.
- Keep scoring/FFT on the laptop if desired; it already runs in browser JavaScript. It does not have to consume roof Pi CPU.

Current investigation folder on the original Mac: `work/tap-score-investigation-2026-11-18/`. The portable archive includes its scripts, findings, JSON and plots. Some scripts have absolute original-Mac paths and require adjustment on another host.

## 16. Batteries, power, connectors and historical hardware context

This section records discussion, not a fresh electrical inspection or a claim that every referenced purchase was installed.

- Loads discussed: solenoid 12 V 2.5 A; each bridge 12 V 1 A; Pi 4 plus camera/USB accessories on regulated ~5 V; separate 5 V LED light.
- Battery proposals included nominal 12 V lithium/LiFePO4 packs and 18 V nominal/20 V MAX DeWalt-style tool batteries, including third-party packs. Final installed battery and verified continuous-current rating are not conclusively recorded here.
- A 10 A continuous-discharge target was a battery-selection margin, not a claim the Pi draws 10 A.
- Buck converter steps down. A nominal 12 V battery varies with chemistry/charge state; regulated 12 V over a below/above-12 V range requires buck-boost or a higher-voltage source plus buck. Do not treat a nominal label as a constant terminal voltage.
- Pi branch uses a separate regulated 5 V supply. A higher current rating does not force that current through a load. A cheap nameplate rating is not proof of sustained output/thermal performance.
- Power planning was parallel branches with common ground: battery positive → nearby fuse → positive-side switch → distribution/regulators; battery negative → ground distribution. The switch opens the positive conductor; it does not short positive to negative.
- User blew 5 A fuses during solenoid wiring and had no regulator output until debugging wiring/connectors. Exact failure cause was not conclusively documented. Measuring each node relative to battery ground was part of troubleshooting.
- Battery F2 tabs use matching female quick-disconnects/crimps. PP45/Anderson-style connectors were discussed for convenient battery swaps and reduced wear on battery tabs.
- Match normal/mini blade fuses to the actual holder. Final holder size/current rating is not verified in this handoff.
- Bridge barrel measurement: approximately 2 mm ID and 5.46 mm OD, suggesting 5.5x2.1 mm rather than 5.5x2.5 mm, but verify actual fit/polarity rather than treating rough caliper data as a guarantee.
- LED was later clarified as 5 V; Pi-buck branch capacity and wiring should account for it. A Pi 5 V header pin exists but is not a separately regulated high-current LED driver.
- Pi power through GPIO bypasses parts of the input path. USB-C does not make the Pi accept arbitrary input voltage. An old Pi was suspected damaged, but no cause was proven.
- An emergency stop was discussed, then the user explicitly asked to leave it off. Do not invent an installed E-stop. The UI Stop and ordinary power switch are separate features.

Historical shopping references for identification (current listings/specs not revalidated):

| Topic | References |
|---|---|
| Remote relay HAT | Amazon `B0FB8VML77` |
| Solenoid MOSFET | `B0FMJH3DML`; earlier `B0DZP27C2N` |
| X motor / driver | `B0G4C4KKTB`, `B07K9N85LK` |
| Encoder reference | `B093QFXFT8` |
| 5 V light | `B0FQHX58V2` |
| Pi buck option | `B09DGDQ48H` |
| Regulator discussion | `B0GBGLNR52`, linked target also `B07WFMKMV9` — mismatch was in the user's pasted link |
| Battery proposals | `B0FSYSD625`, `B09H3FG1D5`, `B0H3QFKWXQ`, `B0DLG9KNWL` |
| Tool battery proposals | `B0GT4G1Q2X`, `B0GDTZZ82F`; genuine 5 Ah reference `B00KWRM5Z4` |
| Charger discussion | `B0D8W1THH6` |
| F2 leads / connector harness | `B07MFHFQ5L`, `B0FDFTDT23` |
| USB-C bare-wire reference | `B0FNX18TNC` |
| Ethernet switch proposals | `B0H1W14VT3`, `B0D9JBTBZB` |

## 17. Deployment, installation and recovery

### Existing Pis: update only what changed

1. Confirm the laptop route to `.4` uses inspection Ethernet.
2. Check roof/robot status using GET endpoints. Keep role addresses fixed.
3. Back up installed files to a timestamped directory under `/var/backups/brick-mapper/`.
4. Upload changed files to a staging folder in the Pi user's home, then install with sudo into the paths above. Check hashes/syntax as appropriate.
5. Roof UI files go to `/opt/brick-mapper/app/`; roof motion modules go to `/opt/blueteam-network/`.
6. Restart only the necessary service. Restart `brick-mapper.service` for server/static-token changes and `blueteam-network.service` for controller modules. Changing camera/audio capture may require their robot services.
7. Refresh the browser after a roof web-service restart.
8. Verify read-only status/UI. Do not use a smoke script that accidentally sends real moves/taps.

Do not run full network/reimage installers for every JS edit. Preserve `/opt/blueteam-network/network.key`, `/var/lib/brick-mapper-outrigger/`, DHCP reservations and the operator's browser evidence.

The latest successful winch UI deployment installed exactly `app/dashboard.js`, `app/map.html`, `pi/outrigger.py` and `pi/winch.py`, with a backup at `/var/backups/brick-mapper/20261119T015415Z-winch-ui`. The UTC date is Nov 19; this was Nov 18 evening in America/New_York. Earlier backups include `*-tap-completion`, `*-adaptive-winch`, `*-manual-feedback`, and the local pre-page-rearrangement archive.

### Fresh pair / reimage

- Both Pis need compatible Raspberry Pi OS and enabled SSH. Fresh images may have different SSH host keys; verify identity rather than blindly deleting known-host records.
- Use one shared controller key on both Pis. Existing location: `/opt/blueteam-network/network.key`. It is not the SSH password or browser token.
- Base roof install uses `pi/install.sh roof blueteam2`; roof app installation uses `pi/install-roof.sh blueteam2`.
- Roof dependencies include dnsmasq/Avahi and Python GPIO packages (`python3-gpiozero`, `python3-lgpio`), plus serial access through `dialout`.
- Robot needs FFmpeg, USB capture tools as needed, the ARM64 MediaMTX binary, and GPIO/PWM support. `pi/install-robot.sh blueteam3` installs its role/services; audio setup uses `pi/install-audio.sh blueteam3 [ALSA_CARD_ID]`.
- `pi/mediamtx` and `pi/network.key` are deliberately external/ignored installation inputs. A source clone is not a complete preinstalled SD-card image.
- Program Nano Every/Pico once. Their saved firmware starts automatically. Normal users do not need to connect them to a laptop.
- Reboot where the installer requires applying Ethernet/PWM overlays. A card swap can bring the wrong role/address to a Pi, so check OS role/service configuration, not only the physical box name.

### Shared-key availability

Fresh handoff audits could not retrieve the currently installed key because both SSH connections timed out. A **historical local key** was found at `work/brick-mapper-direct/data/network.key`, SHA-256 `ce69319ad3132ab4633300885e12543a2dba69c108f42dfe3e8911cf020e4711`. It is included in the local portable bundle as `private/legacy-network.key`; it is not in the public repository. Its match to the current Pi pair is **unverified**. Do not overwrite working deployed keys with it on that assumption.

For a fresh isolated pair, generate a new shared key and install the same file on both. For the existing pair, preserve/reuse the actual installed key when access returns. Do not rotate one Pi alone and then interpret failed acknowledgements as a Wi-Fi fault.

### Known installer/documentation caveats

- Some historical README paragraphs describe older pulse timing, one-tab audio storage or UI labels. This handoff and current code are more current.
- The older `install-poweroff.sh --update` file list may not copy newly added `pages.js` on its own. For the full current roof UI use `install-roof.sh` or explicitly install all current app files.
- `/etc/brick-mapper/x-axis.json` null pin values do not mean the current Nano's D5/D6 wiring is absent.
- Do not copy the old CSI `rpiCamera` configuration over the USB stream setup.

### Shutdown and SD cards

**Shut down both Pis** requests robot shutdown first, then roof shutdown after acknowledgement. The poweroff timer/polkit mechanism avoids granting arbitrary shell commands to the web page. Wait for completion before unplugging power. If the robot cannot acknowledge, the roof stays online and reports the failure.

An earlier roof SD card stopped booting/networking. Reflashing the same card restored operation, suggesting a filesystem/software state issue rather than proving destroyed hardware. A read-only e2fsck found free-block/inode/checksum/orphan bookkeeping issues, but journal recovery was skipped, so it was not a conclusive forensic root-cause diagnosis. Abrupt power removal was a concern. Do not claim the app wrote too much data at once and caused corruption; permanent inspection evidence is browser-side in the current architecture.

## 18. Offline preview and tests

No LAN required:

```sh
bash 'preview/Start Preview.command'
# Open http://127.0.0.1:8765/
bash 'preview/Stop Preview.command'
```

The scripts find the current folder, bind localhost only, track their owned PID, check app identity, and avoid killing unrelated services. `BRICK_PREVIEW_BROWSER=0` suppresses automatic browser opening. State/log files are under `${TMPDIR}/brick-mapper-preview-<uid>/`.

Run focused committed no-hardware tests:

```sh
python3 -m unittest discover -s tests -v
node --check app/dashboard.js
```

Nine winch unit tests passed after the tuning UI/backend changes. They cover response updates, no-progress growth behavior, relay ordering/cancellation/nonoverlap, pulse bounds, saved tuning, no movement while applying, persistence across controller construction, and preserving calibration/zero.

Other recent checks, preserved in the portable bundle when available:

- Isolated Chrome tests for all three pages, camera prominence, stationary capture, photo/audio/score saving, PDF/export behavior and duplicate suppression.
- Audio survives page refresh, playable WAV URLs, backup/restore and idempotent imports.
- Simulated slow-save reproduction of the tapping lock; corrected version releases tapping.
- Old save finishing while a new tap is pending does not clear the new tap's busy state.
- Failed save leaves WAV available and a retry option.
- Storage open/write timeouts abort and permit retry.
- Negative Y movement works in the simulator.
- Adaptive model comparison with delayed encoder readings/coasting; improved examples were **simulated**, not physical performance guarantees.
- Live browser checks blocked/intercepted POSTs so they did not move motors or tap.
- Final deployed slider check loaded Up 400 ms, Down 250 ms, adaptation on, Apply available.

Temporary original-Mac test paths include `/private/tmp/brick-mapper-smoke.py`, `brick-audio-persistence-check.py`, `brick-tap-save-live-fixed-check.py`, `brick-adaptive-winch/`, and `brick-winch-ui/`. They are not durable deployment dependencies. Some harnesses assume Chrome at `/Applications/Google Chrome.app/Contents/MacOS/Google Chrome`, Python `websocket-client`, and original absolute paths; review/adjust before running elsewhere.

### Do not accidentally run an old live smoke suite

The old `brick-mapper-smoke.py` contains default scenarios after a `results=[]` marker and can call photo/preview operations. Other copied tests can invoke real control endpoints if pointed at the live site without interception. Read the bodies and ensure all action requests are mocked or intentionally authorized. Prefer the committed no-hardware unit suite for a quick handoff sanity check.

## 19. Outstanding work and where to start next

1. Recover current network access and read actual saved `winchTuning`. The user may have tuned it after the final verified deployment.
2. Validate the winch pulse settings on the physical mechanism. The previous tiny probes did not reliably move it, and the user explicitly wants quick direct tuning from UI, not repeated redeployments.
3. Investigate recurring Pico serial stoppage if it returns. Current workaround is USB reconnect; no permanent root cause is proven.
4. Keep X and standalone tapping usable when Y zero is missing. Distinguish no encoder, no zero, movement busy, recording busy and storage busy in messages.
5. Consider robust handling of browser tokens after a server restart so old live-video tabs do not have mysteriously dead command buttons.
6. Validate saved-tuning UI on the actual operator's browser if they report range/save issues. The code/unit roundtrip and deployed load were checked; no physical slider-driven winch test was done by the assistant.
7. Improve signal scoring only with explicit agreement to change the supplied logic; begin with clean repeated same-spot recordings and the included investigation.
8. Implement a microphone-level calibration UI if requested; it is currently an idea, not a deployed feature.
9. Build robust live mortar/tapper detection and visual map alignment if desired. Current real brick IDs are encoder/reference-based and vulnerable to slip; preview identity is synthetic.
10. Clarify/reconcile the remaining old README/installer assumptions as normal maintenance, without changing operating behavior during a live demo.

### Suggested first prompt for the next Codex instance

> Continue the Brick Mapper project using this handoff and the current `camera testing/pi-hosted` source. Read the latest audit before assuming the Pis are online. Preserve the roof-hosted/browser-storage architecture and the user's preference for quick, narrow live changes. Inspect current saved winch settings and encoder status over the Ethernet LAN. Do not alter the supplied scoring logic or trigger physical motion as part of routine checks. Report what is deployed versus only local, and ask for hardware reconnection only when necessary. My next requested task is: [user supplies task].

## 20. Handoff integrity and limits

The main document is intentionally broad because much of the operational context was spread across a long chat. Precise current values should come from source and live configuration, not from assumptions about old screenshots, filenames, branch names or booted SD cards.

The portable archive's manifests identify every included source file by size and SHA-256. Its Git patch covers tracked changes; untracked source files are present in the full source snapshot. Audit failures remain visible rather than being filled with invented live values. Original user references are preserved unmodified. No new movement, tap, gain setting or network reconfiguration was performed while preparing this handoff.
