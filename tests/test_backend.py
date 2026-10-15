from pathlib import Path
import binascii
import os
import sys
if sys.platform != 'win32':
    import pty
import select
import struct
import tempfile
import threading
import time
import unittest
import zlib

from rfd_native.backend import (Cancelled, Firmware, NativeSerial, RadioClient,
                                RadioError, RadioIdentity, SerialTimeout, xmodem_packet,
                                parse_rssi, parse_settings)
from rfd_native.backend import rssi_unavailable_reason


def gbl(payload=b'firmware'):
    prefix = struct.pack('<II', 0x03A617EB, 8) + bytes.fromhex('0000000301010000')
    prefix += struct.pack('<II', 0xFE0101FE, len(payload)) + payload
    prefix += struct.pack('<II', 0xFC0404FC, 4)
    return prefix + struct.pack('<I', zlib.crc32(prefix))


class BootloaderTransport:
    """Validates frames as an independent receiver, including duplicate packets."""
    baud = 57600

    def __init__(self, *, nak_first=False, lose_ack=False, cancel_block=None,
                 reject_eot=False, nak_eot=False, missing_acks=False):
        self.rx = bytearray()
        self.writes = []
        self.received = bytearray()
        self.expected = 1
        self.packets = 0
        self.nak_first = nak_first
        self.lose_ack = lose_ack
        self.cancel_block = cancel_block
        self.reject_eot = reject_eot
        self.nak_eot = nak_eot
        self.missing_acks = missing_acks
        self.eot_count = 0
        self.event = None

    def read(self, size, timeout):
        if not self.rx:
            raise SerialTimeout('Simulated missing ACK')
        result = bytes(self.rx[:size])
        del self.rx[:size]
        return result

    def write(self, data, timeout=3):
        self.writes.append(bytes(data))
        if data == b'UPLOAD\r\n':
            # Ready and several CRC requests arrive in a single OS read.
            self.rx.extend(b'UPLOAD\r\nReady\r\nCCC')
        elif len(data) == 133:
            self.packets += 1
            assert data[0] == 1
            assert data[1] ^ data[2] == 255
            assert binascii.crc_hqx(data[3:131], 0) == struct.unpack('>H', data[131:])[0]
            if self.cancel_block == self.expected:
                self.rx.append(0x18)
                return
            if self.missing_acks:
                return
            if self.nak_first:
                self.nak_first = False
                self.rx.append(0x15)
                return
            if data[1] == self.expected & 255:
                self.received.extend(data[3:131])
                self.expected += 1
            else:
                assert data[1] == (self.expected - 1) & 255, 'Sequence advanced before ACK'
            if self.lose_ack:
                self.lose_ack = False
                return
            self.rx.append(0x06)
            if self.event:
                self.event.set()
        elif data == b'\x04':
            self.eot_count += 1
            self.rx.append(0x18 if self.reject_eot else 0x15 if self.nak_eot and self.eot_count == 1 else 0x06)

    def discard(self):
        self.rx.clear()

    def set_baud(self, baud):
        if baud != 57600 and b'BOOTNEW\r\n' not in self.writes:
            raise AssertionError('Transfer must remain at 57600 baud')
        self.baud = baud

    def close(self):
        pass


class UploadClient(RadioClient):
    def __init__(self, transport, **kwargs):
        super().__init__(transport, **kwargs)
        self.timing_scale = 0
        self.post_identity = RadioIdentity('RFD900X2', 'RFD Async 4.03 on RFD900X2', 'AT command', 57600)

    def enter_bootloader(self):
        self.identity = RadioIdentity('RFD900X2', 'Bootloader', 'Bootloader', 57600)
        return self.identity

    def enter_at(self):
        return self.post_identity


class FirmwareTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'test rfd900x2.gbl'
        self.path.write_bytes(gbl())

    def tearDown(self):
        self.tmp.cleanup()

    def test_valid_gbl_and_corruption(self):
        Firmware.load(self.path)
        data = bytearray(self.path.read_bytes())
        data[25] ^= 1
        self.path.write_bytes(data)
        with self.assertRaisesRegex(RadioError, 'CRC32'):
            Firmware.load(self.path)

    def test_wrong_target_and_truncated_file(self):
        wrong = self.path.with_name('rfd900ux2.gbl')
        wrong.write_bytes(gbl())
        with self.assertRaisesRegex(RadioError, 'filename'):
            Firmware.load(wrong)
        self.path.write_bytes(gbl()[:-3])
        with self.assertRaisesRegex(RadioError, 'Truncated'):
            Firmware.load(self.path)

    def test_xmodem_crc_and_padding(self):
        self.assertEqual(binascii.crc_hqx(b'123456789', 0), 0x31C3)
        packet = xmodem_packet(256, b'abc')
        self.assertEqual(packet[:3], bytes([1, 0, 255]))
        self.assertEqual(packet[3:131], b'abc' + b'\x26' * 125)
        self.assertEqual(struct.unpack('>H', packet[-2:])[0], binascii.crc_hqx(packet[3:131], 0))

    def test_full_upload_wraps_block_number_without_extra_block(self):
        # More than 256 frames, and the final GBL length is exactly divisible by 128.
        payload = bytes(range(256)) * 129 + b'P' * 92
        self.path.write_bytes(gbl(payload))
        fw = Firmware.load(self.path)
        self.assertEqual(len(fw.data) % 128, 0)
        transport = BootloaderTransport()
        updates = []
        client = UploadClient(transport)
        identity = client.upload(fw, lambda *args: updates.append(args), ack_timeout=.01)
        self.assertEqual(bytes(transport.received), fw.data)
        self.assertEqual(transport.packets, len(fw.data) // 128)
        self.assertEqual(updates[-1], (len(fw.data), len(fw.data)))
        self.assertEqual(identity.model, 'RFD900X2')
        self.assertIn(b'BOOTNEW\r\n', transport.writes)

    def test_retries_nak_and_lost_ack_without_duplicate_data(self):
        for options in ({'nak_first': True}, {'lose_ack': True}):
            with self.subTest(options=options):
                transport = BootloaderTransport(**options)
                fw = Firmware.load(self.path)
                UploadClient(transport).upload(fw, ack_timeout=.01)
                self.assertEqual(transport.received[:len(fw.data)], fw.data)
                self.assertEqual(transport.packets, 2)
                self.assertEqual(transport.writes[1], transport.writes[2])

    def test_retry_exhaustion_never_boots_unconfirmed_image(self):
        transport = BootloaderTransport(missing_acks=True)
        with self.assertRaisesRegex(RadioError, 'not acknowledged'):
            UploadClient(transport).upload(Firmware.load(self.path), retries=3, ack_timeout=.01)
        self.assertEqual(transport.packets, 3)
        self.assertNotIn(b'BOOTNEW\r\n', transport.writes)
        self.assertIn(b'\x18\x18', transport.writes)

    def test_cancel_and_receiver_rejection_never_boot(self):
        for receiver_cancel in (False, True):
            with self.subTest(receiver_cancel=receiver_cancel):
                self.path.write_bytes(gbl(b'a' * 300))
                transport = BootloaderTransport(cancel_block=1 if receiver_cancel else None)
                event = threading.Event()
                if not receiver_cancel:
                    transport.event = event
                with self.assertRaises((Cancelled, RadioError)):
                    UploadClient(transport, cancel=event).upload(Firmware.load(self.path), ack_timeout=.01)
                self.assertNotIn(b'BOOTNEW\r\n', transport.writes)
                self.assertIn(b'\x18\x18', transport.writes)

    def test_eot_must_be_acknowledged(self):
        transport = BootloaderTransport(reject_eot=True)
        with self.assertRaisesRegex(RadioError, 'completed image'):
            UploadClient(transport).upload(Firmware.load(self.path), ack_timeout=.01)
        self.assertNotIn(b'BOOTNEW\r\n', transport.writes)
        transport = BootloaderTransport(nak_eot=True)
        UploadClient(transport).upload(Firmware.load(self.path), ack_timeout=.01)
        self.assertEqual(transport.eot_count, 2)

    def test_changed_file_is_rejected_before_any_serial_write(self):
        firmware = Firmware.load(self.path)
        self.path.write_bytes(gbl(b'different'))
        transport = BootloaderTransport()
        with self.assertRaisesRegex(RadioError, 'changed'):
            UploadClient(transport).upload(firmware)
        self.assertEqual(transport.writes, [])

    def test_no_success_if_firmware_does_not_answer_after_transfer(self):
        transport = BootloaderTransport()
        client = UploadClient(transport)
        client.post_identity = None
        with self.assertRaisesRegex(RadioError, 'did not answer ATI'):
            client.upload(Firmware.load(self.path), ack_timeout=.01)

    def test_old_firmware_response_is_not_reported_as_success(self):
        versioned = self.path.with_name('RFDAsync V4.03 rfd900x2.gbl')
        versioned.write_bytes(gbl())
        client = UploadClient(BootloaderTransport())
        client.post_identity = RadioIdentity('RFD900X2', 'RFD SiK 3.57 on RFD900X2', 'AT command', 57600)
        with self.assertRaisesRegex(RadioError, 'not verified as running'):
            client.upload(Firmware.load(versioned), ack_timeout=.01)
        self.assertEqual(client.identity.version, 'RFD SiK 3.57 on RFD900X2')

    def test_async_post_upload_verification_discovers_default_baud(self):
        class AsyncClient(UploadClient):
            def enter_at(self):
                return RadioIdentity('RFD900X2', 'RFD ASYNC 4.03 on RFD900X2', 'AT command',115200) if self.port.baud == 115200 else None
        transport = BootloaderTransport()
        result = AsyncClient(transport).upload(Firmware.load(self.path),ack_timeout=.01)
        self.assertEqual(result.baud,115200)
        self.assertEqual(transport.baud,115200)


class RadioDashboardTests(unittest.TestCase):
    report = 'L/R RSSI: -82/-88 L/R noise: -110/-112 pkts: 17 txe=1 rxe=2 stx=3 srx=4 ecc=5/6 temp=29 dco=7 avd=8 Tx/Dly:27 /Set:2 future=9'

    def test_close_resumes_data_mode_even_after_cancellation(self):
        class Port:
            def __init__(self): self.writes=[]; self.closed=False
            def write(self,data,timeout=3): self.writes.append(data)
            def close(self): self.closed=True
        for mode in ('AT command','Bootloader'):
            port=Port()
            client=RadioClient(port)
            client.identity=RadioIdentity('RFD900X2','test',mode,57600)
            client.cancel.set()
            client.close()
            self.assertTrue(port.closed)
            self.assertEqual(port.writes,[b'ATO\r'] if mode=='AT command' else [])

    def test_all_signal_fields_including_future_metrics_and_signed_values(self):
        values = parse_rssi(self.report)
        self.assertEqual(len(values),17)
        self.assertEqual(values['Local RSSI'],-82)
        self.assertEqual(values['Remote noise'],-112)
        self.assertEqual(values['ecc (2)'],6)
        self.assertEqual(values['Tx/Dly'],27)
        self.assertEqual(values['future'],9)
        self.assertEqual(parse_rssi('ERROR'),{})

    def test_firmware_registers_keep_names_instead_of_fixed_offsets(self):
        sik = parse_settings('S3:NETID=25\nS4:TXPOWER=30\nR0:TARGET_RSSI_dBm=-80')
        async_settings = parse_settings('S9:NETID=25\nS12:TXPOWER=10')
        self.assertEqual(sik[0].register,'S3')
        self.assertEqual(async_settings[0].register,'S9')
        self.assertEqual(sik[2].value,'-80')

    def test_monitor_stops_without_changing_saved_settings(self):
        class Client(RadioClient):
            def enter_at(self): return RadioIdentity('RFD900X2','RFD SiK 3.57 on RFD900X2','AT command',57600)
            def query(self, command, timeout=1):
                self.command=command
                return RadioDashboardTests.report
        client=Client(None)
        stop=threading.Event()
        samples=[]
        def callback(*sample):
            samples.append(sample)
            stop.set()
        client.monitor_rssi(callback,stop)
        self.assertEqual(len(samples),1)
        self.assertEqual(client.command,'ATI7')

    def test_async_rssi_is_reported_as_unsupported_without_waiting_or_debug_commands(self):
        class Client(RadioClient):
            def enter_at(self): return RadioIdentity('RFD900X2','RFD ASYNC 4.03 on RFD900X2','AT command',115200)
            def query(self,*_): raise AssertionError('Do not query unsupported RSSI')
            def send(self,*_): raise AssertionError('Do not enable debug on unsupported firmware')
        client=Client(None)
        with self.assertRaisesRegex(RadioError,'Async text RSSI reports are unavailable'):
            client.monitor_rssi(lambda *_:None,threading.Event())
        with self.assertRaisesRegex(RadioError,'Async text RSSI reports are unavailable'):
            client.rssi()
        self.assertEqual(rssi_unavailable_reason(RadioIdentity('RFD900X2','RFD SiK 3.57','AT command',57600)),'')

    def test_save_rejects_unknown_register_before_any_writes(self):
        class Client(RadioClient):
            def settings(self): return 'S1:SERIAL_SPEED=57\nS9:NETID=25'
            def query(self,*args): raise AssertionError('Must validate all edits first')
        with self.assertRaisesRegex(RadioError,'not an editable'):
            Client(None).save_settings({'S9':'26','S3':'100'})

    def test_save_reboots_then_checks_actual_register_values(self):
        class Client(RadioClient):
            def __init__(self):
                super().__init__(None)
                self.calls=[]
            def settings(self):
                return 'S1:SERIAL_SPEED=115\nS9:NETID='+('26' if 'ATZ' in self.calls else '25')
            def query(self,cmd,timeout=1):
                self.calls.append(cmd)
                return 'OK'
            def send(self,cmd): self.calls.append(cmd.decode().strip())
            def pause(self,_): pass
        client=Client()
        stages=[]
        self.assertIn('NETID=26',client.save_settings({'S9':'26'},lambda step,_:stages.append((step,list(client.calls)))))
        self.assertEqual(client.calls,['ATS9=26','AT&W','ATZ'])
        self.assertEqual([step for step,_ in stages],[0,0,1,2,2,3,4])
        self.assertEqual(next(calls for step,calls in stages if step==2),['ATS9=26','AT&W'])
        class Disconnected(Client):
            def settings(self):
                if 'ATZ' in self.calls:
                    raise RadioError('Radio did not reconnect')
                return super().settings()
        stages=[]
        with self.assertRaisesRegex(RadioError,'did not reconnect'):
            Disconnected().save_settings({'S9':'26'},lambda step,_:stages.append(step))
        self.assertIn(2,stages)  # EEPROM save was acknowledged before reboot.
        self.assertNotIn(3,stages)
        self.assertNotIn(4,stages)
        client=Client()
        self.assertIn('NETID=26',client.save_settings({}))
        self.assertEqual(client.calls,['AT&W','ATZ'])


class PtyModem:
    def __init__(self):
        self.master, self.slave = pty.openpty()
        self.path = os.ttyname(self.slave)
        self.running = True
        self.mode = 'transparent'
        self.buffer = bytearray()
        self.commands = []
        self.thread = threading.Thread(target=self.loop, daemon=True)
        self.thread.start()

    def respond(self, text):
        os.write(self.master, text)

    def loop(self):
        while self.running:
            ready, _, _ = select.select([self.master], [], [], .05)
            if not ready:
                continue
            try:
                data = os.read(self.master, 4096)
            except OSError:
                return
            self.buffer.extend(data)
            if self.buffer.endswith(b'+++') and self.mode == 'transparent':
                self.buffer.clear()
                self.mode = 'at'
                self.respond(b'OK\r\n')
            while b'\r' in self.buffer or b'\n' in self.buffer:
                positions = [p for p in (self.buffer.find(b'\r'), self.buffer.find(b'\n')) if p >= 0]
                pos = min(positions)
                cmd = bytes(self.buffer[:pos]).decode('ascii', errors='replace')
                del self.buffer[:pos + 1]
                if not cmd:
                    continue
                self.commands.append(cmd)
                if self.mode == 'at':
                    answers = {'ATI': 'RFD SiK 3.57 on RFD900X2', 'ATI2': '132',
                               'ATI5': 'S1:SERIAL_SPEED=57\r\nS3:NETID=25', 'ATI7': 'RSSI: 42/40'}
                    if cmd == 'AT&UPDATE':
                        self.mode = 'boot'
                        self.respond(b'RFD900xSub:2 Bootloader\r\n')
                    elif cmd in answers:
                        self.respond((cmd + '\r\n' + answers[cmd] + '\r\n').encode())
                elif self.mode == 'boot':
                    if cmd == 'CHIPID':
                        self.respond(b'CHIPID\r\nRFD900xSub:2 ChipID:1234\r\n')
                    elif cmd == 'RESET':
                        self.mode = 'transparent'

    def close(self):
        self.running = False
        self.thread.join(timeout=1)
        os.close(self.master)
        os.close(self.slave)


@unittest.skipIf(sys.platform == 'win32', 'POSIX pseudo-terminal integration tests')
class NativeSerialIntegrationTests(unittest.TestCase):
    def test_cancel_connection_releases_native_port_promptly(self):
        master, slave = pty.openpty()
        path = os.ttyname(slave)
        port = NativeSerial(path)
        cancel = threading.Event()
        results = []
        client = RadioClient(port,cancel=cancel)
        def connect():
            try:
                client.identify()
            except Exception as exc:
                results.append(exc)
            finally:
                client.close()
        worker = threading.Thread(target=connect)
        worker.start()
        try:
            time.sleep(.05)
            start = time.monotonic()
            cancel.set()
            worker.join(timeout=.6)
            self.assertFalse(worker.is_alive())
            self.assertLess(time.monotonic()-start,.6)
            self.assertIsInstance(results[0],Cancelled)
            self.assertEqual(port.fd,-1)
            reopened = NativeSerial(path)
            reopened.close()
        finally:
            cancel.set()
            worker.join(timeout=2)
            os.close(master)
            os.close(slave)

    def test_cancel_baud_scan_stops_before_switching_to_next_rate(self):
        class Port:
            baud=9600
            def set_baud(self,_): raise AssertionError('Baud changed after cancellation')
        class Client(RadioClient):
            def enter_at(self): return None
        cancel=threading.Event()
        client=Client(Port(),cancel=cancel,phase=lambda message:cancel.set() if message.startswith('Checking firmware') else None)
        with self.assertRaises(Cancelled):
            client.identify()

    def test_native_pty_identity_settings_rssi_and_bootloader_roundtrip(self):
        modem = PtyModem()
        port = None
        try:
            port = NativeSerial(modem.path, 57600)
            client = RadioClient(port)
            client.timing_scale = .01
            identity = client.identify()
            self.assertEqual(identity.model, 'RFD900X2')
            self.assertIn('SERIAL_SPEED=57', client.settings())
            self.assertIn('42/40', client.rssi())
            self.assertEqual(client.enter_bootloader().mode, 'Bootloader')
            self.assertEqual(client.return_to_firmware().mode, 'AT command')
            self.assertNotIn('UPLOAD', modem.commands)
            self.assertNotIn('BAUDHI', modem.commands)
        finally:
            if port:
                port.close()
            modem.close()

    def test_native_empty_read_deadline_and_port_release(self):
        master, slave = pty.openpty()
        path = os.ttyname(slave)
        port = NativeSerial(path)
        try:
            start = time.monotonic()
            self.assertEqual(port.read(128, .04), b'')
            self.assertLess(time.monotonic() - start, .4)
            os.write(master, b'\x00\xff\x06')
            self.assertEqual(port.read(128, .2), b'\x00\xff\x06')
        finally:
            port.close()
            os.close(master)
            os.close(slave)


if __name__ == '__main__':
    unittest.main()
