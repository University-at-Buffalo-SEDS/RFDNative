"""MAVLink wire helpers used by bounded RSSI diagnostics."""
import struct


def crc_x25(data):
    crc=0xffff
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc=(crc>>1)^0x8408 if crc&1 else crc>>1
    return crc


def heartbeat(sequence=0):
    # GCS presence only: no vehicle commands, no arming or control requests.
    body=bytes((9,sequence&255,255,190,0))+struct.pack('<IBBBBB',0,6,8,0,4,3)
    return b'\xfe'+body+struct.pack('<H',crc_x25(body+b'\x32'))


def radio_status_frames(data):
    result=[]
    for offset in range(len(data)-8):
        marker=data[offset]
        if marker not in (0xfe,0xfd):
            continue
        length=data[offset+1]
        header=6 if marker==0xfe else 10
        if offset+header+length+2>len(data):
            continue
        message=data[offset+5] if marker==0xfe else int.from_bytes(data[offset+7:offset+10],'little')
        if message!=109 or length>9 or marker==0xfe and length!=9:
            continue
        payload=data[offset+header:offset+header+length]
        actual=int.from_bytes(data[offset+header+length:offset+header+length+2],'little')
        if actual!=crc_x25(data[offset+1:offset+header+length]+bytes((185,))):
            continue
        rxerrors,fixed,rssi,remote,txbuf,noise,remote_noise=struct.unpack('<HHBBBBB',payload.ljust(9,b'\x00'))
        values={'rxerrors':rxerrors,'fixed':fixed,'TX buffer free (%)':txbuf}
        for name,value in [('Local RSSI',rssi),('Remote RSSI',remote),('Local noise',noise),('Remote noise',remote_noise)]:
            if value!=255:
                values[name]=value
        result.append({'offset':offset,'message':message,'values':values})
    return result
