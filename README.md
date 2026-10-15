# RFD Native

Native Python/Tk radio tools and firmware uploader for **RFD900X2** radios and
`.gbl` images. macOS and Linux use standard-library POSIX serial; Windows uses
pyserial with native COM ports. All platforms use the same XMODEM-CRC upload
and settings backend. No Wine, .NET, or network service is needed.

## Setup and run

Python **3.10 or newer** with Tkinter is required (3.12 recommended). Setup
creates a `.venv` in this source folder. Keep that folder in place after setup;
rerun setup if you move it. Do not commit or copy the virtual environment.

| Platform | Prerequisites | Setup | Launch |
| --- | --- | --- | --- |
| macOS | Install Python with working Tkinter, e.g. the python.org installer | `./setup-macos.command` | `./run-macos.command` or `~/Applications/RFD Native.app` |
| Linux | Python, Tkinter, venv, desktop session, permission to open the UART | `./setup-linux.sh` | `./run-linux.sh` |
| Windows | Python installer with Tcl/Tk and launcher; USB UART driver if needed | Double-click `setup-windows.cmd` | Double-click `run-windows.cmd` |

On Debian/Ubuntu, install prerequisites with
`sudo apt install python3 python3-tk python3-venv`. For serial access, add your
account to the device's group (commonly `sudo usermod -aG dialout "$USER"`), then
log out and back in. Other distributions may use the `uucp` group. Do not run the
GUI as root. On macOS/Linux, set `RFD_PYTHON=/path/to/python3` when running setup
to choose a different interpreter. On Windows, setup installs the pinned
`pyserial==3.5` requirement into `.venv`; internet access is needed for that step.
[pyserial documentation](https://pyserial.readthedocs.io/en/latest/pyserial_api.html)
describes the underlying COM-port API and timeout/flow-control settings.

Setup does not connect to or change a radio. Firmware images and USB adapter
drivers are not bundled; obtain firmware from RFDesign. Select a COM port on
Windows or a `/dev/...` port on macOS/Linux. Close other programs using the port.
For a TX/RX/GND-only connection, the radio's saved RTSCTS setting must be zero;
host-side flow control being disabled does not change the radio's setting.

The macOS build uses the included icon. To regenerate it from source, run
`python3 build_app.py --regenerate-icon` on macOS with Swift installed.

### Per-user files

| Platform | Network plan | Logs |
| --- | --- | --- |
| macOS | `~/Library/Application Support/RFD Native/network-session.json` | `~/Library/Logs/RFD Native/` |
| Linux | `$XDG_DATA_HOME/rfd-native/network-session.json` (default `~/.local/share`) | `$XDG_STATE_HOME/rfd-native/logs/` (default `~/.local/state`) |
| Windows | `%LOCALAPPDATA%\RFD Native\network-session.json` | `%LOCALAPPDATA%\RFD Native\Logs\` |

macOS hardware operation has been exercised. Windows transport is covered by
mocked serial tests; Linux and Windows GUI/hardware behavior still need testing
on those operating systems. POSIX PTY tests are skipped on Windows.

## Open

Open `~/Applications/RFD Native.app`, or run `python3 app.py` in this folder.
Select the USB serial port and the radio's current firmware baud rate (57600
for SiK before this session's firmware change; Async 4.03 now responds at
115200), then **Connect & read settings**. Connecting automatically reads the
radio's settings while keeping the connection screen open. Click **Read settings**
or open the settings tab to view them. Bootloader connections show that
settings are unavailable until installed firmware starts. During connection and
automatic baud detection, the Disconnect button becomes **Cancel connection**.
Cancellation stops the scan, releases the serial port, and returns to an idle UI
without an error popup. Identification and post-upload verification
also try common firmware baud rates automatically. Changing this dropdown changes
the computer's connection speed, not the radio's saved SERIAL_SPEED register.

The supplied `RFDAsync V4.03 rfd900x2.gbl` in Downloads is selected automatically
if present. Choosing a file validates GBL tag structure, final CRC32, and an X2
filename. Encrypted GBL payloads do not expose a readable hardware target;
filename checking does not replace the bootloader's own signature/image checks.

**Flash selected firmware** reviews the exact model and file before writing.
The app captures a settings snapshot first, enters the bootloader, stays at
57600 baud, waits for `Ready` and `C`, and sends 128-byte CRC packets. NAK and
missing-ACK retries resend the same sequence number. The final partial packet
is padded with `0x26`, matching the vendor uploader. End-of-transfer must be
acknowledged before `BOOTNEW`. A firmware identity query follows reboot;
an unresponsive image is reported as unverified, not successful. After a verified
flash, settings are reread automatically without changing tabs. Old settings are
cleared before flashing; a failed refresh is reported separately from firmware
upload verification. Async firmware
defaults to 115200 after a firmware-family change. The original native upload in
this session completed all 810 packets; its post-reboot check incorrectly stayed
at 57600. A subsequent native ATI/ATI2 check confirmed **RFD ASYNC 4.03 on
RFD900X2**, board 132, at 115200. This baud-detection issue is fixed.

## Settings, signal graph, and network setup

After connecting, **Read settings** loads every reported S/R register into the
settings table. Double-click a value to stage an integer edit. **Save edits &
reboot** reviews the changes, sends only changed registers, saves using AT&W,
reboots, and verifies readback. S0 FORMAT is read-only. The Settings tab shows a persistent progress indicator with
timestamped **Sent**, **Saved on radio**, **Reconnected**, and **Verified**
milestones. A failed reconnect retains the acknowledged save milestone and
shows the error without marking reconnect or verification complete. Registers are discovered
by firmware name, so Async and SiK layouts are handled independently. Firmware
validates supported values; accepted edits preceding a rejected command may
remain in RAM, and the app reports that explicitly. No automatic EEPROM restore
is attempted when switching firmware families.

The **RSSI & statistics** tab plots local/remote RSSI and noise over 60 seconds,
shows every parsed diagnostic field and the raw report, and exports the latest
10,000 samples to CSV. Start/Stop run on the serial worker. SiK uses ATI7 polling;
firmware that rejects ATI7 is tried with AT&T=RSSI debug reporting. Debugging is
disabled on stop. No fabricated samples or converted dBm values are shown.
**Async text RSSI monitoring is unavailable in the current app.** Live tests on
Async 4.03 returned ERROR for ATI7 and no text signal report for AT&T=RSSI even
with incoming traffic. RFDesign's current tool also disables its Async RSSI graph.
This is not proof that all RSSI interfaces are absent: the older Async manual
documents heartbeat-triggered MAVLink reports, and its release notes mention RSSI
in SAS packets. Receiver-side tests with MAVLINK=1 plus eight valid GCS heartbeats,
including save/reboot, produced no CRC-valid RADIO_STATUS messages. RX_ENCAP_METHOD=2
including save/reboot produced telemetry but no identifiable extra RSSI envelope.
Both original settings were restored, saved and verified as zero. A controlled
test with a matching peer is still required to confirm the packet interfaces on
4.03; only one USB radio adapter was available. The UI explains this uncertainty.
SiK retains its signal graph. Reports not recognized on other firmware time out
after 10 seconds; telemetry bytes are not treated as signal measurements.

### Serial reception after configuration

AT command mode suspends normal telemetry output. A native test received zero
bytes for two seconds before ATO, then 10,538 bytes in six seconds after ATO,
including the command echo and binary telemetry. Disconnect/close now sends ATO
when leaving a known AT session before releasing the serial port. Bootloader
sessions are left untouched. Serial terminals should use the current radio baud
(115200 for the tested Async 4.03), 8N1, and binary/hex display for this payload.

**Network setup** builds saved JSON plans for a SiK pair or an Async group.
Group fields apply to every radio in the plan. Edits, node destinations, selected
row and historical verified readbacks are saved automatically in
`~/Library/Application Support/RFD Native/network-session.json` and restored at
launch. This also retains incomplete field edits while typing. Radios whose
readbacks differ from the latest group settings are marked as needing upload.
**Export plan** creates a portable JSON copy; **Upload group to selected radio**
stages the current group settings and reviews them before saving to the attached
radio. Upload each radio over USB in turn. Local autosave never writes hardware.
The radio upload button also saves and verifies unchanged current settings,
rather than silently doing nothing. Stop RSSI monitoring before uploading.
Use **Add radio** / **Remove selected**, or enter an initial group size when
creating a plan. The app supports unique Async node IDs 1–32767 and broadcast
destination 32768; it no longer imposes a 100-radio limit. This is address-space
support, not a claim that 32767 nodes can exchange traffic simultaneously. Adding
a third radio offers to move the original pair's destinations to broadcast.
Set common NETID, air speed, frequency range and channel count, create the plan,
then connect/read each radio over USB in turn. Select its row and **Stage selected
row**, review the Settings tab, and save. Async two-radio plans use 1→2 and 2→1;
larger groups use distinct node IDs and destination 32768 (broadcast). Double-click
an Async row to change destination. A new plan copies the connected radio's shared
parameters when available. Install matching firmware separately on every radio.
Plan status records settings readback, not a verified over-the-air link. Remote
RT configuration, specialized GPIO commands, encryption key editing, and MultiComm
master scheduling are not implemented; numeric settings reported by ATI5 are.

Logs and pre-flash settings snapshots use the platform log folder above.
Settings files are reference transcripts, not vendor-tool import files.
Settings/RSSI reads do not write saved settings. An interrupted upload can leave
the radio in bootloader mode; reconnect, identify it, and select the image again.

This is a local uploader, not an official RFDesign release. Initial support is
deliberately limited to RFD900X2, board ID 132, and the X2 GBL bootloader. Do not
use `.bin` files for first-generation radios with this app.

## CLI and tests

```
python3 cli.py ports
python3 cli.py identify --port /dev/cu.usbserial-BG00HPF3
python3 cli.py settings --port /dev/cu.usbserial-BG00HPF3
python3 cli.py bootloader-check --port /dev/cu.usbserial-BG00HPF3
python3 -m unittest discover -s tests -v
python3 build_app.py
```

`bootloader-check` enters bootloader mode and returns to the installed firmware
without sending firmware blocks. CLI flashing requires both `--firmware FILE`
and the explicit `--yes-write-firmware` flag. The GUI's serial worker is isolated
from Tk's main thread; all UI updates go through a queue.

## Protocol references

- [RFDesign X Series Datasheet 2.1, section 6](https://files.rfdesign.com.au/Files/documents/XseriesDatasheet2.1.pdf):
  bootloader serial defaults and `CHIPID`, `UPLOAD`, `BAUDHI`, `BAUDLO`, `BOOTNEW`.
- [RFDesign firmware downloads](https://files.rfdesign.com.au/firmware/): official X2 GBL files.
- [Silicon Labs GBL format](https://docs.silabs.com/mcu-bootloader/3.1.2/bootloader-user-guide-gsdk-4/02-gecko-bootloader-file-format):
  tag framing and file CRC32.
- The installed RFD900Tools 2.67 upload path was inspected to establish its X2
  identification string (`RFD900xSub:2`), 128-byte packet framing, CRC16,
  `0x26` padding, EOT acknowledgement, and final `BOOTNEW` ordering.

Unlike RFD900Tools 2.67, this implementation does not request `BAUDHI` / 1200000
baud. It uses bounded native serial reads throughout. Firmware transfer tests
use an independent simulated receiver; the PTY tests also exercise real POSIX
serial setup, byte I/O, mode transitions, and deadlines without touching hardware.

## Included source and diagnostic tools

- `app.py`, `rfd_native/`: GUI, serial protocol, firmware validation, settings,
  signal graph, network planner, modal dialogs, and platform storage paths.
- `cli.py`: headless radio diagnostics and explicit firmware upload.
- `probe_async.py`: POSIX-only experimental UART/RSSI/flow-control probes.
  Some modes temporarily change settings; read `--help` and the source first.
- `configure_remote_uart.py`: explicitly saves RTSCTS=0 on a radio attached to
  `/dev/ttyAMA0` at 115200 through SSH (`--host HOST`, default `192.168.3.7`).
  Stop the remote UART owner before using it and restart that service afterward.
- `build_app.py`, `make_icon.swift`, `assets/`: macOS bundle and icon sources.
- `setup.py` and platform setup/run scripts: local Python runtime creation and launch.
- `tests/`: protocol, firmware, persistence, transport and POSIX PTY tests.

Run tests with `.venv/bin/python -m unittest discover -s tests -v` on macOS/Linux
or `.venv\Scripts\python.exe -m unittest discover -s tests -v` on Windows.
These tests do not need a physical radio. The diagnostic tools and firmware
upload commands do operate on hardware when invoked.
