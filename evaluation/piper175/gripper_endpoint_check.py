"""Read gripper feedback; optional empty-jaw zero target. Never changes calibration.

--execute-close is only for an operator-confirmed empty, clear gripper.
It requires the gripper already enabled; it never enables joints or sends joint targets.
"""
import argparse
import fcntl
import json
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT/'collection'))


def sample(arm):
    msg=arm.GetArmGripperMsgs();state=msg.gripper_state;flags=state.foc_status
    age=time.time()-msg.time_stamp
    names=('voltage_too_low','motor_overheating','driver_overcurrent','driver_overheating','sensor_status','driver_error_status')
    faults=[name for name in names if getattr(flags,name)]
    row={'time':time.time(),'age_s':age,'position_mm':state.grippers_angle/1000,
         'effort_raw':state.grippers_effort,'enabled':bool(flags.driver_enable_status),'faults':faults}
    if not 0<=age<.3:raise RuntimeError('Stale gripper feedback: '+json.dumps(row))
    if faults:raise RuntimeError('Gripper fault: '+json.dumps(row))
    return row


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--arm',choices=['left','right'],default='left')
    parser.add_argument('--execute-close',action='store_true')
    parser.add_argument('--hold-seconds',type=float,default=0,help='Repeat the same zero target at 50 Hz; maximum 3 seconds')
    args=parser.parse_args()
    if not 0<=args.hold_seconds<=3:parser.error('hold-seconds must be 0..3')
    if args.hold_seconds and not args.execute_close:parser.error('hold requires execute-close')
    from arm import channel_for
    from enable_zero import controllers
    from piper_sdk import C_PiperInterface
    dest=ROOT/'recovery_checks'/('gripper_'+args.arm+'_'+time.strftime('%Y%m%d_%H%M%S'))
    dest.mkdir(parents=True,exist_ok=False)
    result={'side':args.arm,'execute_close':args.execute_close,'joint_commands_sent':0,'gripper_commands_sent':0,
            'calibration_changed':False,'hold_seconds':args.hold_seconds,'target_mm':0 if args.execute_close else None,'samples':[]}
    arm=None
    try:
        with (ROOT/'.recover_and_zero.lock').open('a') as lock, (ROOT/'collection/.relocalize.lock').open('a') as loc:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);fcntl.flock(loc,fcntl.LOCK_EX|fcntl.LOCK_NB)
            if args.execute_close and controllers():raise RuntimeError('Another arm controller is running')
            arm=C_PiperInterface(can_name=channel_for(args.arm));arm.ConnectPort(piper_init=False)
            time.sleep(.8)
            initial=sample(arm);result['initial']=initial
            if args.execute_close:
                if not initial['enabled']:raise RuntimeError('Gripper is not enabled; no automatic recovery')
                # Same effort setting used by the existing collection gripper probe.
                # Last byte 0x00: do not change the gripper zero calibration.
                arm.GripperCtrl(0,1000,0x01,0x00);result['gripper_commands_sent']=1
            deadline=time.monotonic()+args.hold_seconds
            while args.execute_close and time.monotonic()<deadline:
                row=sample(arm);result['samples'].append(row)
                if not row['enabled']:raise RuntimeError('Gripper lost enable feedback')
                arm.GripperCtrl(0,1000,0x01,0x00);result['gripper_commands_sent']+=1
                time.sleep(.02)
            for _ in range(30):
                result['samples'].append(sample(arm));time.sleep(.1)
            result['final']=result['samples'][-1];result['status']='complete'
    except Exception as exc:
        result.update(status='failed',error=str(exc));raise
    finally:
        if arm:arm.DisconnectPort()
        (dest/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
        print(json.dumps({'directory':str(dest),**{k:v for k,v in result.items() if k!='samples'}},ensure_ascii=False))


if __name__=='__main__':main()
