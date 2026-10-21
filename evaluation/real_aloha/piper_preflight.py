"""Read controller state/limits and assess kinematic readiness; never command motion."""
import argparse
import json
import time
from pathlib import Path
import numpy as np
from .piper_kinematic_guard import PiperKinematics


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--can-left',default='can_left')
    parser.add_argument('--can-right',default='can_right')
    args=parser.parse_args()
    # Hardware imports are only loaded on the robot host.
    from pyAgxArm import create_agx_arm_config,AgxArmFactory,ArmModel
    from pyAgxArm.api.constants import ROBOT_MDH_PRESET
    report=dict(physical_commands=0,model='piper',firmware_profile='v188',
                mdh=ROBOT_MDH_PRESET['piper'],arms={},ready=False)
    connected=[]
    try:
        for name,channel in [('left',args.can_left),('right',args.can_right)]:
            arm=AgxArmFactory.create_arm(create_agx_arm_config(
                robot=ArmModel.PIPER,firmeware_version='v188',channel=channel))
            connected.append(arm);arm.connect();time.sleep(.3)
            data=dict(firmware=arm.get_firmware(timeout=1),limits=[],samples=[],blockers=[])
            report['arms'][name]=data
            for i in range(1,7):
                limit=arm.get_joint_angle_vel_limits(i,timeout=1)
                if limit is None:raise RuntimeError(f'{name}: missing joint limit J{i}')
                data['limits'].append([limit.msg.min_angle_limit,limit.msg.max_angle_limit])
            for _ in range(10):
                time.sleep(.05)
                joint,flange,status=arm.get_joint_angles(),arm.get_flange_pose(),arm.get_arm_status()
                if any(s is None or not -.1<=time.time()-s.timestamp<=.5 for s in (joint,flange,status)):
                    raise RuntimeError(f'{name}: missing/stale feedback')
                motors=[arm.get_motor_states(i) for i in range(1,7)]
                data['samples'].append(dict(time=time.time(),joints=list(joint.msg),flange=list(flange.msg),
                    status=int(status.msg.arm_status),control=int(status.msg.ctrl_mode),
                    motors=[None if m is None else dict(position=m.msg.position,velocity=m.msg.velocity,
                        age=time.time()-m.timestamp) for m in motors]))
            last=data['samples'][-1]
            if last['status']!=0:data['blockers'].append(f"controller status={last['status']}; no reset attempted")
            if last['control']!=1:data['blockers'].append(f"control mode={last['control']}; no switch attempted")
            model=PiperKinematics(report['mdh'],data['limits'])
            try:data['sigma_min']=model.check_start(last['joints'])
            except RuntimeError as exc:data['blockers'].append(str(exc))
            xyz=np.array([s['flange'][:3] for s in data['samples']])
            data['observed_drift_m']=float(np.max(np.linalg.norm(xyz-xyz[0],axis=1)))
        report['ready']=all(not d['blockers'] for d in report['arms'].values())
    except Exception as exc:
        report['error']=f'{type(exc).__name__}: {exc}'
        raise
    finally:
        for arm in connected:arm.disconnect()
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps(report,indent=2)+'\n')
        print(json.dumps(report,indent=2))


if __name__=='__main__':main()
