The current Ethernet dashboard runs on the rooftop Pi. See pi-hosted/README.md.
The instructions below are for the earlier local Mac package.

BLUE TEAM BRICK MAPPER — MAC PACKAGE

START
Double-click Start.command. It starts the local viewer and opens Safari.
The map is at http://127.0.0.1:8767/map.html
The connection test viewer is at http://127.0.0.1:8767/

STOP
Double-click Stop.command when finished. Closing Safari alone leaves the
viewer running. Stop.command stops the viewer and its bridge probes.
Status.command tells you whether this folder's viewer is running.
There is no automatic startup, login item, or recurring task.
Stopping this Mac app does not stop the camera services on the Pi.

CAPTURE AND MAP
Enter X and Y encoder counts, choose Good or Bad, and capture a photo.
Positions and labels are manual test inputs. The live video comes from the Pi.
Click a tile to enlarge it, review retakes, or change a label.
The grid uses observed X/Y values; equal cell sizes do not represent distance.

FILES
Photos/                 All packaged and newly captured photos.
data/brick-map.sqlite3   Saved positions, labels, capture times and filenames.
app/                    Viewer and local server source; no outside libraries.
.runtime/               Runtime state and a troubleshooting log, created by Start.

The existing map and its three mapped photos are included.
New captures always save to Photos/. They also get a Desktop copy when
macOS allows Desktop access. The map reads the Photos/ copy, so it keeps
working when Desktop copies are moved or removed.

Keep this entire folder together. Stop the app before moving or copying it.
You can move it to another writable folder; it does not depend on the
original Codex project directory. Back up the whole folder to preserve the map.

REQUIREMENTS
macOS and Python 3.9 or newer. Python is already installed on this Mac.
No pip packages or internet downloads are needed on this Mac.
On another Mac, Python 3 may need to be installed from https://www.python.org/.
The camera Pi still needs its previously configured FFmpeg and MediaMTX
services. This folder packages the Mac viewer, not a Raspberry Pi OS image.

BRIDGE CONNECTION
Camera Pi Ethernet: 192.168.2.3
Mac Ethernet adapter: 192.168.2.254
Subnet mask: 255.255.255.0
Ethernet router and DNS: leave empty
Internet Sharing: OFF
Normal Mac Wi-Fi can stay connected for ordinary internet browsing.
The Pi's Wi-Fi is currently disabled for bridge-only testing.

A replacement Ethernet adapter needs the same manual Mac address.
This app does not change network settings. The bridges keep their own
192.168.2.1 and 192.168.2.2 addresses.

CAMERA SSH
ssh blueteam@192.168.2.3
Use the Pi's existing password; no password is stored in this package.

IF START REPORTS PORT 8767 IS IN USE
Run Stop.command from the folder that started the other copy, then retry.
Start and Stop will not kill an unrelated process on that port.

TERMINAL ALTERNATIVE
After changing into this folder:
  ./Start.command
  ./Stop.command
  ./Status.command

LOGS AND MEASUREMENTS
.runtime/server.log contains startup messages and errors, not a continuous
video-latency record. Use Save connection log in the viewer to export its
in-memory test history before closing the page. HTTP response time is a
network/server measurement, not camera-to-screen latency.
