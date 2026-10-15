#!/usr/bin/env python3
"""Headless diagnostics and explicit firmware upload using the same backend."""
import argparse
from pathlib import Path
import sys
import threading

from rfd_native.backend import Firmware, NativeSerial, RadioClient, serial_ports


def main():
    parser = argparse.ArgumentParser(description='Native RFD900X2 serial diagnostics/uploader')
    parser.add_argument('action', choices=['ports', 'identify', 'settings', 'rssi', 'diagnostics', 'monitor-rssi', 'bootloader-check', 'flash'])
    parser.add_argument('--seconds',type=float,default=10)
    parser.add_argument('--port')
    parser.add_argument('--baud', type=int, default=57600)
    parser.add_argument('--firmware', type=Path)
    parser.add_argument('--yes-write-firmware', action='store_true')
    args = parser.parse_args()
    if args.action == 'ports':
        print('\n'.join(serial_ports()))
        return 0
    if not args.port:
        parser.error('--port is required')
    if args.action == 'flash' and (not args.firmware or not args.yes_write_firmware):
        parser.error('flash requires --firmware and --yes-write-firmware')
    firmware = Firmware.load(args.firmware) if args.action == 'flash' else None
    client = RadioClient(NativeSerial(args.port, args.baud), log=lambda s: print(s, flush=True),
                         phase=lambda s: print('PHASE:', s, flush=True))
    try:
        print('IDENTITY:', client.identify(), flush=True)
        if args.action == 'settings':
            print(client.settings(), flush=True)
        elif args.action == 'rssi':
            print(client.rssi(), flush=True)
        elif args.action == 'diagnostics':
            for command in ('ATI6','ATI7'):
                print(command,client.query(command,2),flush=True)
        elif args.action == 'monitor-rssi':
            stop=threading.Event()
            timer=threading.Timer(args.seconds,stop.set)
            timer.start()
            try:
                client.monitor_rssi(lambda *sample:print('SAMPLE:',sample,flush=True),stop)
            finally:
                timer.cancel()
        elif args.action == 'bootloader-check':
            print('NO FIRMWARE TRANSFER: testing bootloader entry/return', flush=True)
            print('BOOTLOADER:', client.enter_bootloader(), flush=True)
            print('RETURN:', client.return_to_firmware(args.baud), flush=True)
        elif args.action == 'flash':
            print('RESULT:', client.upload(firmware, lambda n, t: print(f'ACKNOWLEDGED: {n}/{t}', flush=True)), flush=True)
        return 0
    except Exception as exc:
        print('ERROR:', exc, file=sys.stderr, flush=True)
        return 1
    finally:
        client.close()


if __name__ == '__main__':
    raise SystemExit(main())
