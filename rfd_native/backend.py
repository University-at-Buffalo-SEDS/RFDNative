"""RFDesign X2 bootloader, using native serial and XMODEM-CRC.

The transfer stays at 57600 baud. POSIX uses the standard library; Windows
uses pyserial. No Wine or .NET is required. Saved settings change only through the explicit save_settings API.
"""
from __future__ import annotations

import binascii
import errno
import glob
import hashlib
import os
from pathlib import Path
import re
import select
import struct
import sys
import threading
import time
from dataclasses import dataclass
from typing import Callable, Protocol
import zlib

if sys.platform != 'win32':
    import fcntl
    import termios


class RadioError(Exception):
    pass


class SerialTimeout(RadioError):
    pass


class Cancelled(RadioError):
    pass


class Transport(Protocol):
    baud: int
    def read(self, size: int, timeout: float) -> bytes: ...
    def write(self, data: bytes, timeout: float = 3.0) -> None: ...
    def set_baud(self, baud: int) -> None: ...
    def discard(self) -> None: ...
    def close(self) -> None: ...


def serial_ports() -> list[str]:
    if sys.platform == 'win32':
        try:
            from serial.tools.list_ports import comports
        except ImportError as exc:
            raise RadioError('Run setup-windows.cmd to install pyserial.') from exc
        return sorted(port.device for port in comports())
    return sorted(set(glob.glob('/dev/cu.usb*') + glob.glob('/dev/cu.SLAB*')
                      + glob.glob('/dev/ttyUSB*') + glob.glob('/dev/ttyACM*')
                      + glob.glob('/dev/ttyAMA*') + glob.glob('/dev/serial/by-id/*')))


class NativeSerial:
    """Nonblocking serial FD. Every read and write has a monotonic deadline."""
    def __init__(self, path: str, baud: int = 57600):
        self.path = path
        self.fd = -1
        self.original = None
        self.baud = baud
        try:
            self.fd = os.open(path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            # macOS's serial driver honors this across non-cooperating apps too.
            exclusive = getattr(termios, 'TIOCEXCL', None)
            if exclusive is not None:
                fcntl.ioctl(self.fd, exclusive)
            self.original = termios.tcgetattr(self.fd)
            self.set_baud(baud)
        except (OSError, termios.error) as exc:
            self.close()
            raise RadioError(f'Cannot open/configure {path}: {exc}. '
                             'Close other serial apps; reconnect USB if configuration is rejected.') from exc

    def set_baud(self, baud: int) -> None:
        speed = getattr(termios, f'B{baud}', None)
        if speed is None:
            raise RadioError(f'Unsupported serial baud rate: {baud}')
        attrs = termios.tcgetattr(self.fd)
        attrs[0] = attrs[1] = attrs[3] = 0
        attrs[2] = termios.CS8 | termios.CREAD | termios.CLOCAL
        attrs[4] = attrs[5] = speed
        attrs[6][termios.VMIN] = 0
        attrs[6][termios.VTIME] = 0
        termios.tcsetattr(self.fd, termios.TCSANOW, attrs)
        self.baud = baud

    def read(self, size: int, timeout: float) -> bytes:
        if self.fd < 0:
            raise RadioError('Serial port is closed.')
        deadline = time.monotonic() + max(0.0, timeout)
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return b''
            try:
                ready, _, _ = select.select([self.fd], [], [], remaining)
                if not ready:
                    return b''
                data = os.read(self.fd, size)
                if not data:
                    raise RadioError('USB serial connection closed unexpectedly.')
                return data
            except (BlockingIOError, InterruptedError):
                continue
            except OSError as exc:
                raise RadioError(f'Serial read failed: {exc}') from exc

    def write(self, data: bytes, timeout: float = 3.0) -> None:
        if self.fd < 0:
            raise RadioError('Serial port is closed.')
        remaining_data = memoryview(data)
        deadline = time.monotonic() + timeout
        while remaining_data:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise SerialTimeout('Serial write timed out. Check USB and radio power.')
            try:
                _, ready, _ = select.select([], [self.fd], [], remaining)
                if not ready:
                    continue
                n = os.write(self.fd, remaining_data)
                if n == 0:
                    raise RadioError('USB serial write returned zero bytes.')
                remaining_data = remaining_data[n:]
            except (BlockingIOError, InterruptedError):
                continue
            except OSError as exc:
                raise RadioError(f'Serial write failed: {exc}') from exc

    def discard(self) -> None:
        termios.tcflush(self.fd, termios.TCIFLUSH)

    def close(self) -> None:
        if self.fd < 0:
            return
        try:
            if self.original is not None:
                try:
                    termios.tcsetattr(self.fd, termios.TCSANOW, self.original)
                except (OSError, termios.error):
                    pass  # A disconnected adapter cannot restore termios.
            release = getattr(termios, 'TIOCNXCL', None)
            if release is not None:
                try:
                    fcntl.ioctl(self.fd, release)
                except OSError:
                    pass
        finally:
            os.close(self.fd)
            self.fd = -1


class WindowsSerial:
    def __init__(self, path: str, baud: int = 57600):
        try:
            import serial
        except ImportError as exc:
            raise RadioError('Run setup-windows.cmd to install pyserial.') from exc
        self._api = serial
        self._port = None
        self.path = path
        self.baud = baud
        try:
            self._port = serial.Serial(path, baudrate=baud, bytesize=8, parity='N',
                                       stopbits=1, timeout=0, write_timeout=3,
                                       xonxoff=False, rtscts=False, dsrdtr=False)
        except (serial.SerialException, ValueError, OSError) as exc:
            self.close()
            raise RadioError(f'Cannot open/configure {path}: {exc}. Close other serial apps.') from exc

    def _check(self):
        if self._port is None or not self._port.is_open:
            raise RadioError('Serial port is closed.')
        return self._port

    def set_baud(self, baud: int) -> None:
        try:
            self._check().baudrate = baud
            self.baud = baud
        except (self._api.SerialException, ValueError, OSError) as exc:
            raise RadioError(f'Cannot configure serial baud rate {baud}: {exc}') from exc

    def read(self, size: int, timeout: float) -> bytes:
        if size <= 0:
            return b''
        port = self._check()
        try:
            # Wait for one byte, then take buffered bytes without waiting to fill size.
            port.timeout = max(0.0, timeout)
            first = port.read(1)
            if not first:
                return b''
            port.timeout = 0
            return first + port.read(min(size - 1, port.in_waiting))
        except (self._api.SerialException, OSError) as exc:
            raise RadioError(f'Serial read failed: {exc}') from exc

    def write(self, data: bytes, timeout: float = 3.0) -> None:
        port = self._check()
        try:
            port.write_timeout = max(0.0, timeout)
            if port.write(data) != len(data):
                raise SerialTimeout('Incomplete serial write. Check USB and radio power.')
        except self._api.SerialTimeoutException as exc:
            raise SerialTimeout('Serial write timed out. Check USB and radio power.') from exc
        except (self._api.SerialException, OSError) as exc:
            raise RadioError(f'Serial write failed: {exc}') from exc

    def discard(self) -> None:
        try:
            self._check().reset_input_buffer()
        except (self._api.SerialException, OSError) as exc:
            raise RadioError(f'Serial discard failed: {exc}') from exc

    def close(self) -> None:
        if self._port is not None:
            try:
                self._port.close()
            finally:
                self._port = None


if sys.platform == 'win32':
    NativeSerial = WindowsSerial


@dataclass(frozen=True)
class Firmware:
    path: Path
    data: bytes
    sha256: str
    family: str
    target: str = 'RFD900X2'

    @property
    def expected_version(self) -> tuple[int, int] | None:
        match = re.search(r'\bV(\d+)\.(\d+)', self.path.name, re.I)
        return (int(match[1]), int(match[2])) if match else None

    @classmethod
    def load(cls, path: str | Path) -> 'Firmware':
        path = Path(path).expanduser().resolve()
        data = path.read_bytes()
        if path.suffix.lower() != '.gbl':
            raise RadioError('RFD900X2 requires a .gbl firmware image.')
        if not re.search(r'rfd900x2(?![a-z0-9])', path.name, re.I):
            raise RadioError('The firmware filename must identify rfd900x2. '
                             'This app currently supports only RFD900X2 hardware.')
        if len(data) < 28 or len(data) > 8 * 1024 * 1024:
            raise RadioError('Unexpected GBL firmware size.')
        pos = 0
        ended = False
        while pos < len(data):
            if pos + 8 > len(data):
                raise RadioError('Truncated GBL tag header.')
            tag, size = struct.unpack_from('<II', data, pos)
            if pos == 0 and (tag != 0x03A617EB or size != 8):
                raise RadioError('Invalid Silicon Labs GBL header.')
            if pos + 8 + size > len(data):
                raise RadioError('Truncated GBL tag payload.')
            if tag == 0xFC0404FC:
                if size != 4 or pos + 12 != len(data):
                    raise RadioError('Invalid GBL end tag or trailing data.')
                ended = True
            pos += 8 + size
        if not ended or zlib.crc32(data) != 0x2144DF1C:
            raise RadioError('GBL CRC32 integrity check failed. Download the image again.')
        name = path.name.lower()
        family = 'Asynchronous mesh' if 'async' in name else (
            'SiK' if 'sik' in name else 'MultiComm' if 'multcomm' in name or 'multicomm' in name else 'Custom')
        return cls(path, data, hashlib.sha256(data).hexdigest(), family)


@dataclass(frozen=True)
class RadioIdentity:
    model: str
    version: str
    mode: str
    baud: int
    board_id: int = 132
    detail: str = ''


def rssi_unavailable_reason(identity: RadioIdentity | None) -> str:
    if identity is not None and 'ASYNC' in identity.version.upper():
        return ('Async text RSSI reports are unavailable on this radio. '
                'The documented MAVLink/SAS packet interfaces have not been verified on this setup. '
                'This app cannot yet graph Async RSSI; serial data reception is separate.')
    if identity is not None and identity.mode != 'AT command':
        return 'Start the installed firmware before monitoring RSSI.'
    return ''


def xmodem_packet(block: int, data: bytes) -> bytes:
    if not 1 <= len(data) <= 128:
        raise ValueError('XMODEM payload must contain 1 to 128 bytes.')
    # RFDesign's uploader pads with 0x26, not the usual CP/M 0x1a.
    payload = data.ljust(128, b'\x26')
    n = block & 0xff
    crc = binascii.crc_hqx(payload, 0)
    return bytes((1, n, 255 - n)) + payload + struct.pack('>H', crc)


@dataclass(frozen=True)
class Setting:
    register: str
    name: str
    value: str


def parse_settings(text: str) -> list[Setting]:
    return [Setting(*m.groups()) for m in re.finditer(
        r'(?m)^\s*([SR]\d+):([^=\r\n]+)=([^\r\n]+)', text)]


def parse_rssi(text: str) -> dict[str, int]:
    values = {}
    for label, keys in [('RSSI', ('Local RSSI', 'Remote RSSI')),
                        ('L/R noise', ('Local noise', 'Remote noise'))]:
        match = re.search(re.escape(label) + r':\s*(-?\d+)\s*/\s*(-?\d+)', text, re.I)
        if match:
            values.update(zip(keys, map(int, match.groups())))
    for match in re.finditer(r'([\w]+)\s*=\s*(-?\d+)(?:\s*/\s*(-?\d+))?', text):
        key, a, b = match.groups()
        if b is None:
            values[key] = int(a)
        else:
            values[key + ' (1)'], values[key + ' (2)'] = int(a), int(b)
    for key in ('pkts', 'Tx/Dly', 'Set'):
        match = re.search(re.escape(key) + r':\s*(-?\d+)', text)
        if match:
            values[key] = int(match[1])
    return values if 'Local RSSI' in values else {}


class RadioClient:
    def __init__(self, port: Transport, log: Callable[[str], None] = lambda _: None,
                 phase: Callable[[str], None] = lambda _: None,
                 cancel: threading.Event | None = None):
        self.port = port
        self.log = log
        self.phase = phase
        self.cancel = cancel or threading.Event()
        self.rx = bytearray()
        self.identity: RadioIdentity | None = None
        self.timing_scale = 1.0
        self.transfer_started = False
        self.upload_waiting = False

    def check_cancel(self) -> None:
        if self.cancel.is_set():
            raise Cancelled('Operation cancelled. The radio may remain in bootloader mode.')

    def pause(self, duration: float) -> None:
        if self.cancel.wait(duration * self.timing_scale):
            self.check_cancel()

    def discard(self) -> None:
        self.rx.clear()
        self.port.discard()

    def send(self, data: bytes, *, binary: bool = False) -> None:
        self.check_cancel()
        if not binary:
            self.log('TX ' + repr(data.decode('ascii', errors='replace')))
        self.port.write(data)

    def read_window(self, duration: float, *, quiet: float | None = None) -> bytes:
        result = bytearray(self.rx)
        self.rx.clear()
        deadline = time.monotonic() + duration
        quiet_deadline = deadline
        while time.monotonic() < min(deadline, quiet_deadline):
            self.check_cancel()
            piece = self.port.read(4096, min(.1, min(deadline, quiet_deadline) - time.monotonic()))
            if piece:
                result.extend(piece)
                if quiet is not None:
                    quiet_deadline = time.monotonic() + quiet
        if result:
            self.log('RX ' + repr(result.decode('ascii', errors='replace')))
        return bytes(result)

    def until(self, token: bytes, timeout: float) -> bytes:
        deadline = time.monotonic() + timeout
        while True:
            self.check_cancel()
            pos = self.rx.find(token)
            if pos >= 0:
                end = pos + len(token)
                result = bytes(self.rx[:end])
                del self.rx[:end]  # Preserve a CRC request received with Ready.
                self.log('RX ' + repr(result.decode('ascii', errors='replace')))
                return result
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                partial = bytes(self.rx[-500:])
                raise SerialTimeout(f'Timed out waiting for {token!r}. Last response: {partial!r}')
            self.rx.extend(self.port.read(4096, min(.1, remaining)))

    def byte(self, timeout: float) -> int:
        deadline = time.monotonic() + timeout
        while not self.rx:
            self.check_cancel()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise SerialTimeout('Timed out waiting for a bootloader acknowledgement.')
            self.rx.extend(self.port.read(4096, min(.1, remaining)))
        value = self.rx[0]
        del self.rx[0]
        return value

    def query(self, command: str, timeout: float = 1.2) -> str:
        self.discard()
        self.send(command.encode('ascii') + b'\r')
        text = self.read_window(timeout, quiet=.18).decode('ascii', errors='replace')
        lines = [s.strip() for s in text.replace('\r', '\n').splitlines()]
        return '\n'.join(s for s in lines if s and s != command)

    def at_identity(self) -> RadioIdentity | None:
        text = self.query('ATI', .7)
        if not re.search(r'RFD.+(?:SiK|Async|MultiComm)|RFD\s+(?:SiK|Async)|RFD900X2', text, re.I):
            return None
        if 'RFD900X2' not in text.upper():
            raise RadioError(f'Unsupported modem: {text}. Only RFD900X2 is supported.')
        board = self.query('ATI2', .7)
        match = re.search(r'(?m)^132\s*$', board)
        if not match:
            raise RadioError(f'Cannot confirm RFD900X2 board ID 132; ATI2 returned {board!r}.')
        return RadioIdentity('RFD900X2', text, 'AT command', self.port.baud)

    def enter_at(self) -> RadioIdentity | None:
        self.discard()
        self.send(b'\r')
        self.read_window(.15)
        identity = self.at_identity()
        if identity:
            self.identity = identity
            return identity
        for _ in range(2):
            # Silence before and after the escape sequence is required by SiK.
            self.read_window(1.25 * self.timing_scale)
            self.send(b'+++')
            self.read_window(1.25 * self.timing_scale)
            self.send(b'\r')
            self.read_window(.15)
            identity = self.at_identity()
            if identity:
                self.identity = identity
                return identity
        return None

    def boot_identity(self) -> RadioIdentity | None:
        self.discard()
        self.send(b'\r\n')
        self.read_window(.1)
        self.send(b'CHIPID\r\n')
        text = self.read_window(1.5, quiet=.25).decode('ascii', errors='replace')
        # The X2 bootloader identifies as RFD900xSub:2, not RFD900X2.
        if re.search(r'RFD900x\s*Sub\s*:\s*2(?!\d)|RFD900X2\b', text, re.I):
            self.identity = RadioIdentity('RFD900X2', 'Bootloader', 'Bootloader', self.port.baud, detail=text.strip())
            return self.identity
        if re.search(r'RFD(?:868|900)', text, re.I):
            raise RadioError(f'Bootloader target is not RFD900X2: {text!r}')
        return None

    def identify(self) -> RadioIdentity:
        self.phase('Identifying radio')
        tried = {self.port.baud}
        identity = self.enter_at()
        if identity:
            return identity
        for baud in dict.fromkeys((115200, 57600, 38400, 19200, 9600, 230400)):
            self.check_cancel()
            if baud in tried:
                continue
            tried.add(baud)
            self.phase(f'Checking firmware at {baud:,} baud')
            self.check_cancel()
            self.port.set_baud(baud)
            identity = self.enter_at()
            if identity:
                return identity
        self.check_cancel()
        self.port.set_baud(57600)
        identity = self.boot_identity()
        if identity:
            return identity
        raise RadioError('No recognized RFD900X2 response. Check the selected port, normal baud rate, '
                         'USB connection, and power. Bootloader uses 57600 baud.')

    def settings(self) -> str:
        identity = self.enter_at()
        if identity is None:
            raise RadioError('Cannot enter AT mode; the radio may be in bootloader mode.')
        self.phase('Reading settings')
        settings = self.query('ATI5', 2.5)
        if 'SERIAL_SPEED' not in settings:
            raise RadioError(f'Incomplete settings response: {settings!r}')
        return identity.version + '\n\n' + settings

    def rssi(self) -> str:
        identity = self.enter_at()
        if identity is None:
            raise RadioError('Cannot enter AT mode to read RSSI.')
        reason = rssi_unavailable_reason(identity)
        if reason:
            raise RadioError(reason)
        self.phase('Reading RSSI')
        result = self.query('ATI7', 1.5)
        if not result or result.strip().upper() == 'ERROR':
            raise RadioError('This firmware did not return a valid ATI7 RSSI report.')
        return result

    def monitor_rssi(self, callback, stop: threading.Event) -> None:
        # ATI7 polling works in command mode without debug-mode transitions or
        # synthetic radio traffic. Keep the same AT session for all samples.
        identity = self.enter_at()
        if identity is None:
            raise RadioError('Cannot enter AT mode to monitor RSSI.')
        reason = rssi_unavailable_reason(identity)
        if reason:
            raise RadioError(reason)
        self.phase('Monitoring RSSI')
        while not stop.is_set():
            self.check_cancel()
            raw = self.query('ATI7', 1.5)
            values = parse_rssi(raw)
            if not values:
                if raw.strip().upper() == 'ERROR':
                    self._monitor_debug_rssi(callback, stop)
                    return
                raise RadioError(f'Firmware did not return a recognized RSSI report: {raw!r}')
            callback(time.time(), values, raw)
            stop.wait(.5)

    def _monitor_debug_rssi(self, callback, stop):
        self.phase('Monitoring RSSI debug reports — waiting for radio traffic')
        self.discard()
        self.send(b'AT&T=RSSI\r')
        # Async firmware can keep the serial interface in command mode after
        # enabling the debug flag. Resume reception so live traffic can produce
        # measurements; do not inject synthetic packets into the user's link.
        self.pause(.2)
        self.send(b'ATO\r')
        buffer = bytearray()
        started = time.monotonic()
        reported = False
        ignored_telemetry = False
        try:
            while not stop.is_set():
                self.check_cancel()
                buffer.extend(self.port.read(4096,.1))
                while b'\n' in buffer or b'\r' in buffer:
                    pos = min(p for p in (buffer.find(b'\r'),buffer.find(b'\n')) if p>=0)
                    raw_line = bytes(buffer[:pos])
                    del buffer[:pos+1]
                    if any(byte < 32 and byte != 9 or byte > 126 for byte in raw_line):
                        if not ignored_telemetry:
                            self.log('Non-text telemetry received; awaiting a recognized RSSI report')
                            ignored_telemetry = True
                        continue
                    line = raw_line.decode('ascii').strip()
                    if not line:
                        continue
                    self.log('RSSI RX ' + line)
                    if line == 'ERROR':
                        raise RadioError('This firmware rejects both ATI7 and AT&T=RSSI; RSSI reporting is unavailable.')
                    values = parse_rssi(line)
                    if values:
                        reported = True
                        callback(time.time(),values,line)
                if len(buffer)>65536:
                    raise RadioError('Unterminated RSSI report exceeds 64 KB.')
                if not reported and time.monotonic()-started > 10:
                    raise RadioError('No recognized RSSI reports received in 10 seconds. '
                                     'The firmware may not support this report format; monitoring was stopped.')
        finally:
            # No EEPROM writes. Disable debugging even when cancellation was set.
            previous = self.cancel
            self.cancel = threading.Event()
            try:
                if self.enter_at() is None:
                    raise RadioError('Could not return from RSSI monitoring to AT mode.')
                reply = self.query('AT&T')
                if 'ERROR' in reply.upper():
                    raise RadioError('Firmware rejected disabling RSSI debugging. Reconnect before other operations.')
            finally:
                self.cancel = previous

    def save_settings(self, changes: dict[str, str], status=None) -> str:
        def report(step, message):
            self.log('Settings: ' + message)
            self.phase(message)
            if status is not None:
                status(step, message)
        report(0, 'Checking current settings before saving')
        current = {s.register: s for s in parse_settings(self.settings())}
        for register, value in changes.items():
            if register not in current or register == 'S0':
                raise RadioError(f'{register} is not an editable reported register.')
            if not re.fullmatch(r'-?\d+', value):
                raise RadioError(f'{register} requires an integer value.')
            if current[register].name == 'SERIAL_SPEED' and int(value) not in (1, 2, 4, 9, 19, 38, 57, 115, 230):
                raise RadioError('This app supports serial baud codes 1, 2, 4, 9, 19, 38, 57, 115, 230.')
        # Validate every requested register before issuing any writes.
        for index, (register, value) in enumerate(changes.items(), 1):
            report(0, f'Sending setting {index}/{len(changes)}: {register} {current[register].name}')
            reply = self.query(f'AT{register}={value}')
            if not re.search(r'(?m)^OK$', reply):
                raise RadioError(f'{register} was rejected: {reply!r}. Earlier accepted edits may remain in RAM; EEPROM was not saved.')
        report(1, f'All {len(changes)} settings sent and accepted; saving on radio' if changes else
               'No value changes needed; saving current settings on radio')
        reply = self.query('AT&W', 2)
        if not re.search(r'(?m)^OK$', reply):
            raise RadioError(f'Settings save was not acknowledged: {reply!r}')
        report(2, 'Settings saved on radio; rebooting')
        self.send(b'ATZ\r')
        self.pause(2)
        for register, value in changes.items():
            if current[register].name == 'SERIAL_SPEED':
                self.port.set_baud({1:1200,2:2400,4:4800,9:9600,19:19200,38:38400,57:57600,115:115200,230:230400}[int(value)])
        report(2, 'Reconnecting to radio and reading saved settings')
        result = self.settings()
        report(3, 'Radio reconnected; checking saved values')
        actual = {s.register: s.value for s in parse_settings(result)}
        if any(actual.get(k) != str(int(v)) for k, v in changes.items()):
            raise RadioError('Settings were saved but readback differs. Read settings before retrying.')
        report(4, 'Settings saved, radio reconnected, and readback verified')
        return result

    def enter_bootloader(self) -> RadioIdentity:
        self.phase('Entering bootloader')
        if self.identity and self.identity.mode == 'Bootloader':
            self.port.set_baud(57600)
            found = self.boot_identity()
            if found:
                return found
        identity = self.enter_at()
        if identity:
            self.discard()
            self.send(b'AT&UPDATE\r\n')
            # Capture any banner at the normal speed before configuring 57600.
            self.read_window(.25)
        self.port.set_baud(57600)
        self.pause(.35)
        for _ in range(3):
            found = self.boot_identity()
            if found:
                self.log('Bootloader confirmed. Transfer will stay at 57600 baud.')
                return found
            self.pause(.25)
        raise RadioError('No confirmed X2 bootloader response. No firmware blocks were sent.')

    def return_to_firmware(self, baud: int = 57600) -> RadioIdentity:
        self.phase('Starting installed firmware')
        self.discard()
        # RESET starts the existing image; BOOTNEW is reserved for completed uploads.
        self.send(b'\r\nRESET\r\n')
        self.pause(1.5)
        self.port.set_baud(baud)
        identity = self.enter_at()
        if identity is None:
            identity = self.identify()
        self.identity = identity
        return identity

    def _ack(self, timeout: float) -> int:
        deadline = time.monotonic() + timeout
        while True:
            value = self.byte(max(.001, deadline - time.monotonic()))
            if value in (0x06, 0x15, 0x18):
                return value
            # Repeated C requests and printable status may be queued at startup.
            if time.monotonic() >= deadline:
                raise SerialTimeout('Bootloader did not acknowledge the packet.')

    def upload(self, firmware: Firmware, progress: Callable[[int, int], None] = lambda *_: None,
               retries: int = 10, ack_timeout: float = 3.0) -> RadioIdentity:
        """Explicitly destructive operation; called only by the GUI's Flash button."""
        if firmware.target != 'RFD900X2':
            raise RadioError('Firmware target is not supported.')
        # Revalidate bytes at the boundary; the dataclass can also be constructed by callers.
        verified = Firmware.load(firmware.path)
        if verified.sha256 != firmware.sha256:
            raise RadioError('Firmware changed after selection. Select it again.')
        self.transfer_started = False
        self.upload_waiting = False
        normal_baud = self.port.baud
        self.enter_bootloader()
        boot_sent = False
        try:
            self.phase('Waiting for bootloader')
            self.discard()
            self.send(b'UPLOAD\r\n')
            self.upload_waiting = True
            self.until(b'Ready\r\n', 8.0)
            deadline = time.monotonic() + 8.0
            while self.byte(max(.001, deadline - time.monotonic())) != ord('C'):
                if time.monotonic() >= deadline:
                    raise SerialTimeout('Bootloader did not request XMODEM CRC transfer.')
            total = len(firmware.data)
            progress(0, total)
            self.phase('Uploading firmware')
            for block, offset in enumerate(range(0, total, 128), start=1):
                self.check_cancel()
                packet = xmodem_packet(block, firmware.data[offset:offset + 128])
                accepted = False
                for attempt in range(1, retries + 1):
                    self.send(packet, binary=True)
                    self.transfer_started = True
                    try:
                        reply = self._ack(ack_timeout)
                    except SerialTimeout:
                        reply = None
                    if reply == 0x06:
                        accepted = True
                        break
                    if reply == 0x18:
                        raise RadioError(f'Bootloader cancelled at block {block}. It may have rejected the image.')
                    self.log(f'Block {block}: {"timeout" if reply is None else "NAK"}; retry {attempt}/{retries}')
                if not accepted:
                    raise RadioError(f'Block {block} was not acknowledged after {retries} attempts. '
                                     'The radio remains in bootloader mode; identify it before retrying.')
                progress(min(offset + 128, total), total)
                if block == 1 or block % 50 == 0 or offset + 128 >= total:
                    self.log(f'ACK block {block}/{(total + 127) // 128}')
            self.phase('Finalizing transfer')
            for attempt in range(retries):
                self.send(b'\x04', binary=True)
                try:
                    reply = self._ack(max(ack_timeout, 5.0))
                except SerialTimeout:
                    reply = None
                if reply == 0x06:
                    break
                if reply == 0x18:
                    raise RadioError('Bootloader rejected/cancelled the completed image.')
            else:
                raise RadioError('The bootloader did not acknowledge end-of-transfer. '
                                 'BOOTNEW was not sent; success is not confirmed.')
            self.upload_waiting = False
            self.log('End-of-transfer acknowledged. Starting the new firmware.')
            self.send(b'\r\n')
            self.pause(.1)
            self.send(b'BOOTNEW\r\n')
            boot_sent = True
            self.phase('Verifying new firmware')
            self.pause(2.0)
            self.port.set_baud(normal_baud)
            identity = self.enter_at()
            if identity is None:
                for baud in dict.fromkeys((115200, 57600, normal_baud)):
                    if baud == self.port.baud:
                        continue
                    self.log(f'Checking new firmware at {baud} baud')
                    self.port.set_baud(baud)
                    identity = self.enter_at()
                    if identity:
                        break
            if identity is None:
                raise RadioError('All blocks and end-of-transfer were acknowledged, but the new firmware '
                                 'did not answer ATI. Reconnect and identify before treating it as successful.')
            self.identity = identity
            expected = firmware.expected_version
            reported = {(int(a), int(b)) for a, b in re.findall(r'(\d+)\.(\d+)', identity.version)}
            if expected and expected not in reported:
                raise RadioError(f'Transfer was acknowledged, but expected firmware version '
                                 f'{expected[0]}.{expected[1]:02d}; radio reports {identity.version!r}. '
                                 'The requested firmware is not verified as running.')
            self.phase('Firmware upload verified')
            self.log('New firmware identity: ' + identity.version)
            return identity
        except BaseException:
            if self.upload_waiting:
                # Abort receiver without resetting it or claiming installation success.
                try:
                    self.port.write(b'\x18\x18', timeout=1.0)
                except Exception:
                    pass
            if self.identity and (not boot_sent or self.identity.mode == 'Bootloader'):
                self.identity = RadioIdentity('RFD900X2', 'Reconnect to check image',
                                              'Unverified after reboot' if boot_sent else 'Bootloader / unknown', 57600)
            raise

    def close(self) -> None:
        try:
            if self.identity and self.identity.mode == 'AT command':
                # Configuration mode pauses serial telemetry. Hand the radio
                # back in data mode so the next terminal can receive it.
                self.port.write(b'ATO\r',timeout=.3)
                self.log('Returned radio to data mode before releasing serial port')
        except Exception as exc:
            self.log('Could not resume data mode on close: ' + str(exc))
        finally:
            self.port.close()
