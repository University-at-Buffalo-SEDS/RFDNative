#!/usr/bin/env python3
"""Bounded native capture; optional temporary Async receive encapsulation tests."""
import argparse
import json
import time
import array
import fcntl
import termios
from pathlib import Path

from rfd_native.backend import NativeSerial, RadioClient, parse_settings
from rfd_native.packet_rssi import heartbeat,radio_status_frames


def capture(client, duration, path, send_heartbeat=False):
    data=bytearray()
    chunks=0
    deadline=time.monotonic()+duration
    next_heartbeat=0
    sequence=0
    while time.monotonic()<deadline:
        if send_heartbeat and time.monotonic()>=next_heartbeat:
            client.send(heartbeat(sequence),binary=True)
            sequence+=1
            next_heartbeat=time.monotonic()+1
        piece=client.port.read(4096,min(.1,deadline-time.monotonic()))
        if piece:
            chunks+=1
            data.extend(piece)
    Path(path).write_bytes(data)
    summary={'bytes':len(data),'chunks':chunks,'seconds':duration,'capture':str(path),'first_32_hex':data[:32].hex(' ')}
    print(json.dumps(summary),flush=True)
    print('CRC-verified RADIO_STATUS:',json.dumps(radio_status_frames(data)),flush=True)
    return data


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('mode',choices=['receive','sas','mav','flow','rts'])
    parser.add_argument('--port',default='/dev/cu.usbserial-BG00HPF3')
    parser.add_argument('--baud',type=int,default=115200)
    parser.add_argument('--heartbeat',action='store_true')
    parser.add_argument('--reboot',action='store_true',help='Save/reboot test setting, then save/reboot original setting')
    parser.add_argument('--keep-flow-off',action='store_true',help='Save RTSCTS=0 permanently after confirmed flow-control diagnostic')
    args=parser.parse_args()
    out=Path.home()/'Library/Logs/RFD Native'
    out.mkdir(parents=True,exist_ok=True)
    client=RadioClient(NativeSerial(args.port,args.baud),log=lambda s:print(s,flush=True) if s.startswith('TX') else None)
    try:
        if args.mode=='receive':
            print('Before ATO:',flush=True)
            capture(client,2,out/'receive-before-ato.bin')
            client.send(b'ATO\r')
            print('After ATO:',flush=True)
            capture(client,6,out/'receive-after-ato.bin')
            return
        if args.mode=='rts':
            state=array.array('i',[0])
            fcntl.ioctl(client.port.fd,termios.TIOCMGET,state,True)
            print('Initial modem bits:',hex(state[0]),'RTS:',bool(state[0]&termios.TIOCM_RTS),flush=True)
            try:
                fcntl.ioctl(client.port.fd,termios.TIOCMBIS,array.array('i',[termios.TIOCM_RTS]))
                client.send(b'ATO\r')
                capture(client,8,out/'receive-rts-asserted.bin')
            finally:
                if not state[0]&termios.TIOCM_RTS:
                    fcntl.ioctl(client.port.fd,termios.TIOCMBIC,array.array('i',[termios.TIOCM_RTS]))
            return
        print(client.identify(),flush=True)
        settings={s.name:s for s in parse_settings(client.settings())}
        target=settings[{'sas':'RX_ENCAP_METHOD','mav':'MAVLINK','flow':'RTSCTS'}[args.mode]]
        (out/f'async-{args.mode}-original.json').write_text(json.dumps({k:s.value for k,s in settings.items()},indent=2))
        value={'sas':'2','mav':'1','flow':'0'}[args.mode]
        if args.keep_flow_off:
            if args.mode!='flow': raise RuntimeError('--keep-flow-off requires flow mode')
            result=client.save_settings({target.register:'0'})
            print('Saved settings readback:',result,flush=True)
            client.send(b'ATO\r')
            # Avoid a second ATO on close now that data mode is active.
            client.identity=None
            raw=capture(client,8,out/'receive-flow-off-verified.bin')
            if len(raw)<=5:
                raise RuntimeError('RTSCTS=0 was saved, but telemetry reception was not confirmed')
            print('Verified: RTSCTS=0 saved; telemetry received after reboot',flush=True)
            return
        try:
            reply=client.query(f'AT{target.register}={value}')
            print('Temporary setting response:',repr(reply),flush=True)
            if 'OK' not in reply:
                raise RuntimeError('Temporary setting rejected')
            if args.reboot:
                if 'OK' not in client.query('AT&W',2): raise RuntimeError('Test setting save failed')
                client.send(b'ATZ\r')
                client.pause(2)
                if client.enter_at() is None: raise RuntimeError('Test setting reconnect failed')
                print('Test setting after reboot:',client.query(f'AT{target.register}?'),flush=True)
            client.send(b'ATO\r')
            capture(client,8,out/f'async-{args.mode}{"-heartbeat" if args.heartbeat else ""}.bin',args.heartbeat)
        finally:
            if client.enter_at() is None:
                raise RuntimeError('Could not restore AT session; settings were not saved to EEPROM')
            reply=client.query(f'AT{target.register}={target.value}')
            print('Restore:',target.name,target.value,repr(reply),flush=True)
            if 'OK' not in reply:
                raise RuntimeError('Original setting restore was not acknowledged')
            if args.reboot:
                if 'OK' not in client.query('AT&W',2): raise RuntimeError('Original setting save failed')
                client.send(b'ATZ\r')
                client.pause(2)
                if client.enter_at() is None: raise RuntimeError('Original setting reconnect failed')
            print('Restored readback:',repr(client.query(f'AT{target.register}?')),flush=True)
    finally:
        client.close()


if __name__=='__main__':
    main()
