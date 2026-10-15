"""Transport contract checks without requiring Windows or physical radios."""
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from rfd_native.backend import RadioError, SerialTimeout, WindowsSerial
from rfd_native.paths import data_directory, log_directory


class FakeSerialException(OSError):
    pass


class FakeTimeout(FakeSerialException):
    pass


class FakePort:
    is_open = True
    timeout = 0
    in_waiting = 2

    def read(self, size):
        return b'x' * size

    def write(self, data):
        return len(data)

    def reset_input_buffer(self):
        self.discarded = True

    def close(self):
        self.is_open = False


class WindowsTransportTests(unittest.TestCase):
    def setUp(self):
        self.raw = FakePort()
        self.api = SimpleNamespace(Serial=lambda *a, **kw: self.open_port(kw),
                                   SerialException=FakeSerialException,
                                   SerialTimeoutException=FakeTimeout)
        self.modules = patch.dict(sys.modules, serial=self.api)
        self.modules.start()
        self.addCleanup(self.modules.stop)
        self.port = WindowsSerial('COM12', 115200)
        self.addCleanup(self.port.close)

    def open_port(self, options):
        self.options = options
        return self.raw

    def test_binary_reads_and_disabled_handshake(self):
        self.assertFalse(self.options['rtscts'])
        self.assertFalse(self.options['xonxoff'])
        self.assertFalse(self.options['dsrdtr'])
        self.assertEqual(self.options['bytesize'], 8)
        self.assertEqual(self.port.read(4096, .1), b'xxx')
        self.assertEqual(self.raw.timeout, 0)
        self.assertEqual(self.port.read(1, .1), b'x')
        self.port.discard()
        self.assertTrue(self.raw.discarded)
        self.port.set_baud(57600)
        self.assertEqual(self.raw.baudrate, 57600)

    def test_write_timeout_and_partial_transfer_are_errors(self):
        self.raw.write = lambda data: len(data) - 1
        with self.assertRaises(SerialTimeout):
            self.port.write(b'firmware')
        def timeout(data):
            raise FakeTimeout('driver timeout')
        self.raw.write = timeout
        with self.assertRaises(SerialTimeout):
            self.port.write(b'firmware', .2)
        self.assertEqual(self.raw.write_timeout, .2)

    def test_disconnect_is_reported_and_close_is_idempotent(self):
        def disconnected(size):
            raise FakeSerialException('device removed')
        self.raw.read = disconnected
        with self.assertRaises(RadioError):
            self.port.read(10, .1)
        self.port.close()
        self.port.close()
        with self.assertRaises(RadioError):
            self.port.write(b'x')


class StoragePathTests(unittest.TestCase):
    def test_linux_respects_xdg(self):
        with patch('rfd_native.paths.sys.platform', 'linux'), patch.dict(
                os.environ, XDG_DATA_HOME='/tmp/rfd-data', XDG_STATE_HOME='/tmp/rfd-state'):
            self.assertEqual(data_directory(), Path('/tmp/rfd-data/rfd-native'))
            self.assertEqual(log_directory(), Path('/tmp/rfd-state/rfd-native/logs'))

    def test_windows_local_appdata(self):
        with patch('rfd_native.paths.sys.platform', 'win32'), patch.dict(
                os.environ, LOCALAPPDATA='/tmp/local-appdata'):
            self.assertEqual(data_directory(), Path('/tmp/local-appdata/RFD Native'))
            self.assertEqual(log_directory(), data_directory() / 'Logs')

    def test_macos_preserves_existing_session_location(self):
        with patch('rfd_native.paths.sys.platform', 'darwin'):
            self.assertEqual(data_directory(), Path.home() / 'Library/Application Support/RFD Native')
            self.assertEqual(log_directory(), Path.home() / 'Library/Logs/RFD Native')
