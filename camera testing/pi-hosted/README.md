# Brick Mapper on the rooftop Pi

Plug a computer into the dedicated inspection switch. Use automatic IPv4 addressing (DHCP) for Ethernet, then open [Brick Mapper](http://brick-mapper.local/) in a browser with WebRTC support. If the name does not resolve, open [the inspection IP](http://192.168.2.4/).

A built-in Ethernet port or any USB Ethernet adapter recognized by the computer will work. The rooftop Pi supplies the address over the wired link. The browser needs no laptop server, adapter selection, or internet connection. Use the same URL and browser profile when you want to see a saved map again.

The dashboard shows live video, microphone playback, sample encoder positions, simulated winch controls, and the inspection map. **Tap once** sends one solenoid pulse. **Start auto tap** repeats at the selected rate and defaults to 2 Hz. **Stop tap** stops the output. Automatic tapping also stops if the active browser tab loses contact with the controller. Each pulse runs at about 490 Hz, with 50 ms at 210/255 duty and 30 ms at 50/255 duty before going idle.

The browser keeps the map and its images on the computer. Each capture also downloads a uniquely named PNG. **Back up map** downloads the map and its photos as one file, which **Import map** can restore on another computer or browser profile.

## Pi setup

The rooftop Pi uses `192.168.2.4/24` on `eth0` and gives client computers addresses from `192.168.2.100` to `192.168.2.199`. The robot Pi uses `192.168.2.3/24`. Keep the inspection switch separate from the building LAN because the rooftop Pi runs its own DHCP server.

1. Copy this folder to both Pis. For an existing installation, reuse its controller key. For a fresh pair, generate one key with `umask 077; python3 -c 'import secrets; print(secrets.token_urlsafe(48))' > pi/network.key`. Place the same file at `pi/network.key` on both Pis and keep it private.
2. On the rooftop Pi, install `dnsmasq-base` and `avahi-daemon` if needed. Run `sudo bash pi/install.sh roof YOUR_PI_USER` to configure its Ethernet address and controller. Reboot if the address changed, reconnect at `192.168.2.4`, and run `sudo bash pi/install-roof.sh YOUR_PI_USER`. For an existing rooftop controller with the correct address, the second script suffices.
3. On the robot Pi 4, connect the USB camera and optional USB microphone. Install `ffmpeg` if needed. Extract the ARM64 Linux `mediamtx` binary from the [MediaMTX release](https://github.com/bluenviron/mediamtx/releases/tag/v1.21.0) into `pi/mediamtx`. Run `sudo bash pi/install-robot.sh YOUR_PI_USER` and reboot to apply its Ethernet address and hardware PWM settings.

## Solenoid wiring

| Pi or power connection | MOSFET driver connection |
|---|---|
| GPIO18, physical pin 12 | `TRIG/PWM` |
| Ground, physical pin 14 | Signal `GND` |
| Fused regulated 12 V output | `DC+` and `DC−` |
| Solenoid coil | `OUT+` and `OUT−` |

Use a 3.3 V-compatible driver and a flyback diode rated for the coil current. The diode stripe faces `OUT+`. Check the PWM signal with the 12 V side off before powering the coil. The robot installer reserves GPIO18 for hardware PWM and disables onboard analog audio. The USB microphone uses a separate input.
