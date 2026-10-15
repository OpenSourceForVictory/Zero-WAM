"""Sense-to-gripper 0..80 mm opening test. Default dry run never opens CAN.

Execute mode sends only GripperCtrl commands; never joint enable/control/zero.
CAN must already be UP. Ending leaves the last target and gripper enabled.
"""
import argparse
import fcntl
import json
import os
from pathlib import Path
import signal
import time

from collect import channels, conflicting_processes
from core import load_profile
from grip_mapping import gripper_target
from inspect_usb import snapshot
from sense_reader import SenseReader


def gripper_feedback(arm):
    msg=arm.GetArmGripperMsgs()
    if not 0<=time.time()-msg.time_stamp<.2: raise RuntimeError('Gripper feedback stale')
    state=msg.gripper_state
    flags=state.foc_status
    for name in ('voltage_too_low','motor_overheating','driver_overcurrent','driver_overheating','sensor_status','driver_error_status'):
        if getattr(flags,name): raise RuntimeError('Gripper fault: '+name)
    position=state.grippers_angle/1e6
    if not 0<=position<=.08: raise RuntimeError('Gripper feedback outside 0..80 mm probe envelope')
    return position


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--execute',action='store_true')
    ap.add_argument('--seconds',type=float,default=60)
    args=ap.parse_args()
    if not 0<args.seconds<float('inf'): ap.error('Duration must be positive and finite')
    root=Path(__file__).resolve().parent
    p=load_profile(root/'profile_single_left.json')
    cfg=json.loads((root/'sense_left_endpoints.json').read_text())
    lock=(root/'.teleop.lock').open('w'); fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    if conflicting_processes(): raise RuntimeError('Another controller is running')
    import subprocess
    processes=subprocess.check_output(['ps','-eo','args'],text=True)
    if any(x in processes for x in ('piper_single_ctrl','piper_IK.py','serial_gripper_imu')):
        raise RuntimeError('Existing legacy controller or Sense reader')
    usb=snapshot()
    # A D405 uniquely identifies the Sense assembly; its USB2 companion hub shares the port path.
    camera=next((d for d in usb['devices'] if d.get('serial')==cfg['sense_d405_serial']),None)
    tty=Path(p['serial_ports'][0]).resolve().name
    tty_path=(Path('/sys/class/tty')/tty/'device').resolve()
    serial_usb=next((x for x in tty_path.parents if (x/'idVendor').exists()),None)
    if not camera or serial_usb is None or camera['path'].split('-',1)[1].rsplit('.',1)[0]!=serial_usb.name.split('-',1)[1].rsplit('.',1)[0]:
        raise RuntimeError('Sense calibration identity/serial port mismatch')
    dest=root/'acceptance'/time.strftime('gripper_probe_%Y%m%d_%H%M%S'); dest.mkdir()
    (dest/'usb.json').write_text(json.dumps(usb,indent=2))
    report={'execute':args.execute,'joint_commands_sent':False,'gripper_commands':0,
            'calibration_identity_record':cfg,'mapping':'pika_ros geometry; Piper 0..80 mm clamp','target_range_m':[0.,.08],'target_slew_m_s':None,
            'effort_sdk_units':1000,'scope':'absolute opening probe, mechanical travel not yet validated'}
    arm=None; reader=None; stopped=False
    def stop(*_):
        nonlocal stopped
        stopped=True
    old={sig:signal.signal(sig,stop) for sig in (signal.SIGINT,signal.SIGTERM)}
    try:
        reader=SenseReader(p['serial_ports'][0])
        if args.execute:
            from piper_sdk import C_PiperInterface
            arm=C_PiperInterface(can_name=channels(p)[0])
            arm.ConnectPort(piper_init=False)
            time.sleep(.5)
            base=gripper_feedback(arm)
        else: base=.035  # Explicit virtual jaw position for command generation test.
        target=base; last_raw=None; raw=None; opening=None; last_cmd=None; previous_raw=None
        start=time.monotonic(); last_update=start; count=0
        print('PROBE STARTED '+('EXECUTE' if args.execute else 'DRY RUN, NO CAN'),flush=True)
        with (dest/'samples.jsonl').open('w') as log:
            while not stopped and time.monotonic()-start<args.seconds:
                now=time.monotonic()
                for row in reader.read():
                    encoder=row['AS5047']
                    if 'error' in encoder: raise RuntimeError('Sense encoder error')
                    raw=float(encoder['rad'])
                    opening=min(1.,max(0.,raw/1.67))
                    previous_raw=raw
                    last_raw=time.monotonic()
                now=time.monotonic()
                if last_raw is None:
                    if now-start>.5: raise RuntimeError('No Sense samples')
                    continue
                if now-last_raw>.2: raise RuntimeError('Sense stream stale')
                if now-last_update<.02: continue
                feedback=gripper_feedback(arm) if arm else None
                target=gripper_target(raw)
                if arm:
                    if last_cmd is not None and now-start>1.5 and not arm.GetArmGripperMsgs().gripper_state.foc_status.driver_enable_status:
                        raise RuntimeError('Gripper did not report enabled')
                    arm.GripperCtrl(round(target*1e6),1000,0x01,0x00)
                    report['gripper_commands']+=1
                log.write(json.dumps({'time':time.time(),'raw_rad':raw,'opening':opening,
                                      'target_m':target,'feedback_m':feedback,
                                      'tracking_error_m':None if feedback is None else target-feedback})+'\n')
                last_cmd=target; last_update=now; count+=1
        report.update(status='stopped' if stopped else 'complete',samples=count,base_m=base,last_target_m=last_cmd)
        if count<10: raise RuntimeError('Insufficient input samples')
    except BaseException as exc:
        report.update(status='failed',error=repr(exc))
        raise
    finally:
        try:
            if reader: reader.close()
        finally:
            if arm: arm.DisconnectPort()
            for sig,handler in old.items(): signal.signal(sig,handler)
            (dest/'result.json').write_text(json.dumps(report,indent=2))
            print(json.dumps({'directory':str(dest),**report},indent=2),flush=True)


if __name__=='__main__': main()
