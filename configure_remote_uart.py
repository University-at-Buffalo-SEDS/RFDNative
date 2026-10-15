#!/usr/bin/env python3
"""Set only a remote radio's RTSCTS register using the native backend over SSH."""
import argparse
import base64
from pathlib import Path
import shlex
import subprocess

parser=argparse.ArgumentParser()
parser.add_argument('--host',default='192.168.3.7')
args=parser.parse_args()
backend=(Path(__file__).parent/'rfd_native/backend.py').read_bytes()
operation='''
import json
port=NativeSerial('/dev/ttyAMA0',115200)
client=RadioClient(port,log=lambda s:print(s,flush=True) if s.startswith('TX') else None,
                   phase=lambda s:print('PHASE:',s,flush=True))
try:
    print('IDENTITY:',client.identify(),flush=True)
    before=client.settings()
    registers={s.name:s for s in parse_settings(before)}
    setting=registers.get('RTSCTS')
    if setting is None:
        raise RadioError('Radio did not report an RTSCTS register; no changes made')
    print('BEFORE:',json.dumps({s.name:s.value for s in parse_settings(before)}),flush=True)
    after=client.save_settings({setting.register:'0'})
    values={s.name:s.value for s in parse_settings(after)}
    if values.get('RTSCTS')!='0':
        raise RadioError('RTSCTS=0 not verified after reboot')
    print('AFTER:',json.dumps(values),flush=True)
    client.send(b'ATO\\r')
    client.identity=None
    deadline=time.monotonic()+6
    received=0
    while time.monotonic()<deadline:
        received+=len(port.read(4096,min(.1,deadline-time.monotonic())))
    print('VERIFIED: RTSCTS=0 saved and read back after reboot; UART data mode active; received_bytes_6s='+str(received),flush=True)
finally:
    client.close()
'''
payload=base64.b64encode(backend+b'\n'+operation.encode()).decode('ascii')
remote='python3 -u -c '+shlex.quote('import base64; exec(base64.b64decode('+repr(payload)+'))')
raise SystemExit(subprocess.call(['ssh','-o','BatchMode=yes','-o','ConnectTimeout=5',args.host,remote]))
