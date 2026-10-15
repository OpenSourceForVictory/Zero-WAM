"""Piper S-V1.7-3 status and bounded single-joint commissioning commands."""
import argparse
import json
import math
from pathlib import Path
import time

PORTS = {'usb22': '1-2.2:1.0', 'usb21': '1-2.1:1.0'}
# Confirmed onsite on 2026-10-12: the arm being taught on usb21 is right.
PORTS.update(left=PORTS['usb22'], right=PORTS['usb21'])


def channel_for(name):
    matches = [p.name for p in Path('/sys/class/net').iterdir()
               if (p / 'device').resolve().name == PORTS[name]]
    if len(matches) != 1:
        raise RuntimeError(f'Expected one CAN adapter at {PORTS[name]}, found {matches}')
    return matches[0]


def feedback(arm):
    joints, status = arm.get_joint_angles(), arm.get_arm_status()
    now = time.time()
    if joints is None or status is None or any(not 0 <= now - x.timestamp < .3 for x in (joints, status)):
        raise RuntimeError('Missing or stale arm feedback')
    if len(joints.msg) != 6 or not all(math.isfinite(v) for v in joints.msg):
        raise RuntimeError('Invalid joint feedback')
    return joints, status


def check_motion(arm):
    joints, status = feedback(arm)
    if int(status.msg.arm_status) != 0:
        raise RuntimeError(f'Arm fault: {status.msg}')
    if int(status.msg.ctrl_mode) not in (0, 1) or int(status.msg.teach_status) != 0:
        raise RuntimeError('Arm is in teaching/other control mode; exit it at the robot first')
    err = status.msg.err_status
    for i in range(1, 7):
        if getattr(err, f'joint_{i}_angle_limit') or getattr(err, f'communication_status_joint_{i}'):
            raise RuntimeError(f'Joint {i} reports a limit or communication fault')
    return list(joints.msg)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('command', choices=['status', 'enable', 'jog', 'stop'])
    p.add_argument('--arm', choices=list(PORTS), required=True, help='left/right or physical USB port alias')
    p.add_argument('--joint', type=int, choices=range(1, 7))
    p.add_argument('--delta-deg', type=float)
    p.add_argument('--execute', action='store_true', help='Send the requested enable/jog/stop; otherwise preview')
    args = p.parse_args()
    if args.command == 'jog' and (args.joint is None or args.delta_deg is None or not math.isfinite(args.delta_deg) or not 0 < abs(args.delta_deg) <= 2):
        p.error('jog requires --joint 1..6 and nonzero --delta-deg within [-2, 2]')
    from pyAgxArm import create_agx_arm_config, AgxArmFactory, ArmModel
    channel = channel_for(args.arm)
    arm = AgxArmFactory.create_arm(create_agx_arm_config(
        robot=ArmModel.PIPER, firmeware_version='default', interface='socketcan', channel=channel))
    arm.connect()
    sent = False
    try:
        time.sleep(.5)
        if args.command == 'status':
            joints, status = feedback(arm)
            print(json.dumps({'arm': args.arm, 'usb': PORTS[args.arm], 'channel': channel,
                'joint_deg': [math.degrees(v) for v in joints.msg], 'joint_hz': joints.hz,
                'enabled': arm.get_joints_enable_status_list(),
                'firmware': arm.get_firmware(timeout=2), 'status': str(status.msg)}, indent=2))
            return
        if args.command == 'stop':
            print('Electronic stop: arm may descend under damping; this is not a position hold.')
            if args.execute:
                arm.electronic_emergency_stop()
            return
        current = check_motion(arm)
        if args.command == 'enable':
            print('Enable', args.arm, channel)
            if args.execute:
                for _ in range(100):
                    check_motion(arm)
                    if arm.enable():
                        print('All six motors enabled')
                        break
                    time.sleep(.05)
                else:
                    raise RuntimeError('Enable timeout')
            return
        target = current.copy()
        target[args.joint - 1] += math.radians(args.delta_deg)
        print(json.dumps({'channel': channel, 'target_deg': [math.degrees(x) for x in target], 'speed_percent': 1, 'execute': args.execute}))
        if not args.execute:
            return
        if not all(arm.get_joints_enable_status_list()):
            raise RuntimeError('Motors are not all enabled; enable explicitly first')
        arm.set_speed_percent(1)
        sent = True
        arm.move_j(target)
        deadline = time.monotonic() + 8
        consecutive = 0
        while time.monotonic() < deadline:
            actual = check_motion(arm)
            consecutive = consecutive + 1 if max(abs(a-b) for a,b in zip(actual,target)) < math.radians(.2) else 0
            if consecutive >= 5:
                print('Target reached from fresh joint feedback')
                return
            time.sleep(.05)
        raise RuntimeError('Target arrival timeout')
    except BaseException:
        if sent:
            arm.electronic_emergency_stop()
            print('Motion interrupted: electronic stop sent (damped descent possible).')
        raise
    finally:
        # No implicit home, reset, disable, or gripper motion.
        arm.disconnect()


if __name__ == '__main__':
    main()
