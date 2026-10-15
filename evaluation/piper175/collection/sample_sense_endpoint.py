"""Read raw Sense endpoint angles without sending serial commands or setting zero."""
import argparse
import fcntl
import json
import math
import os
from pathlib import Path
import re
import select
import statistics
import subprocess
import termios
import time


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('endpoint',choices=['open','closed','motion'])
    parser.add_argument('--port',default='/dev/pika-sense-left')
    args=parser.parse_args()
    port=str(Path(args.port).resolve(strict=True))
    busy=subprocess.run(['fuser',port],capture_output=True,text=True)
    if busy.returncode != 1:
        raise RuntimeError('Serial port occupied or ownership check failed: '+busy.stdout+busy.stderr)
    root=Path(__file__).resolve().parent/'acceptance'/time.strftime('endpoint_%Y%m%d_%H%M%S')
    root.mkdir(parents=True,exist_ok=False)
    fd=os.open(port,os.O_RDWR|os.O_NOCTTY|os.O_NONBLOCK)
    old=None; exclusive=False; data=bytearray()
    try:
        old=termios.tcgetattr(fd)
        fcntl.ioctl(fd,termios.TIOCEXCL); exclusive=True
        attr=termios.tcgetattr(fd)
        attr[0]=0; attr[1]=0; attr[2]=termios.CS8|termios.CREAD|termios.CLOCAL; attr[3]=0
        attr[4]=attr[5]=termios.B460800
        attr[6][termios.VMIN]=attr[6][termios.VTIME]=0
        termios.tcsetattr(fd,termios.TCSANOW,attr)
        termios.tcflush(fd,termios.TCIFLUSH)
        print('SAMPLING NOW: '+args.endpoint,flush=True)
        deadline=time.monotonic()+(10 if args.endpoint=='motion' else 5)
        while time.monotonic()<deadline:
            if select.select([fd],[],[],.1)[0]: data.extend(os.read(fd,8192))
    finally:
        try:
            if old is not None: termios.tcsetattr(fd,termios.TCSANOW,old)
        finally:
            try:
                if exclusive: fcntl.ioctl(fd,termios.TIOCNXCL)
            finally: os.close(fd)
    (root/'raw.bin').write_bytes(data)
    # Match the encoder object specifically, not an unrelated 'rad' field.
    values=[]
    for match in re.finditer(rb'"AS5047"\s*:\s*(\{[^{}]*\})',data):
        obj=json.loads(match[1])
        value=obj.get('rad')
        if 'error' not in obj and isinstance(value,(int,float)) and math.isfinite(value):
            values.append(value)
    result={'endpoint':args.endpoint,'port':port,'samples':len(values),'directory':str(root),
            'minimum':min(values) if values else None,'maximum':max(values) if values else None,
            'median':statistics.median(values) if values else None,
            'stable_sample':len(values)>=100 and max(values)-min(values)<=.05 if values else False,
            'scope':'endpoint sample only; no calibration or robot command applied'}
    if args.endpoint=='motion':
        from grip_mapping import normalized_opening
        cfg=json.loads((Path(__file__).parent/'sense_left_endpoints.json').read_text())
        mapped=[]; errors=0
        for value in values:
            try: mapped.append(normalized_opening(value,cfg['closed_rad'],cfg['open_rad']))
            except ValueError: errors+=1
        result.update(mapping_errors=errors,
                      normalized_range=[min(mapped),max(mapped)] if mapped else None,
                      max_adjacent_raw_step=max((abs(b-a) for a,b in zip(values,values[1:])),default=None),
                      scope='offline normalized mapping check only; no robot output')
        result['motion_screen_passed']=bool(len(values)>=100 and not errors and mapped
            and min(mapped)<=.05 and max(mapped)>=.95
            and result['max_adjacent_raw_step']<=.15)
        (root/'mapped.json').write_text(json.dumps({'raw_rad':values,'valid_normalized':mapped}))
    (root/'result.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))
    return 0 if result.get('motion_screen_passed',result['stable_sample']) else 2


if __name__=='__main__': raise SystemExit(main())
