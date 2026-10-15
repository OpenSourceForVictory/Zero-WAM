"""Low-speed joint-zero move; never writes encoder calibration or gripper targets."""
import argparse
import json
import math
from pathlib import Path
import signal
import time

from arm import channel_for, check_motion


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--arm', choices=['left', 'right'], required=True)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    from pyAgxArm import create_agx_arm_config, AgxArmFactory, ArmModel
    channel = channel_for(args.arm)
    arm = AgxArmFactory.create_arm(create_agx_arm_config(
        robot=ArmModel.PIPER, firmeware_version='default', interface='socketcan', channel=channel))
    report = {'arm': args.arm, 'channel': channel, 'speed_percent': 1,
              'target_deg': [0]*6, 'execute': args.execute, 'reached': False,
              'calibration_written': False, 'gripper_commands_sent': False}
    actuated = False
    def interrupt(signum, frame):
        raise KeyboardInterrupt(f'Signal {signum}')
    old = {s: signal.signal(s, interrupt) for s in (signal.SIGINT, signal.SIGTERM)}
    stop_path = Path(__file__).parent / 'inference_client/runtime_config.json'
    def checked():
        if stop_path.exists() and json.loads(stop_path.read_text()).get('emergency_stop', False):
            raise RuntimeError('Runtime stop flag set')
        return check_motion(arm)
    try:
        arm.connect()
        time.sleep(.5)
        fw = arm.get_firmware(timeout=2)
        report['firmware'] = fw
        if fw is None or fw.get('software_version') != 'S-V1.7-3':
            raise RuntimeError('Unexpected firmware')
        report['initial_deg'] = list(map(math.degrees, checked()))
        print(json.dumps(report), flush=True)
        if not args.execute:
            return
        actuated = True
        for _ in range(100):
            checked()
            if arm.enable():
                break
            time.sleep(.05)
        else:
            raise RuntimeError('Enable timeout')
        arm.set_speed_percent(1)
        arm.set_auto_set_motion_mode_enabled(True)
        arm.set_motion_mode('j')
        for _ in range(30):
            checked()
            if int(arm.get_arm_status().msg.mode_feedback) == 1:
                break
            time.sleep(.1)
        else:
            raise RuntimeError('MOVE_J mode transition timeout')
        time.sleep(.2)
        # This firmware ignored the initial single target sent at mode switch.
        # Repeat only the identical authorized zero target after mode feedback.
        for _ in range(25):
            checked()
            arm.move_j([0.0]*6)
            time.sleep(.02)
        report['sent_at'] = time.time()
        deadline = time.monotonic() + 90
        stable = 0
        last_print = 0
        with (args.output/'feedback.jsonl').open('w') as log:
            while time.monotonic() < deadline:
                angles = list(map(math.degrees, checked()))
                if not all(arm.get_joints_enable_status_list()):
                    raise RuntimeError('Motor enable feedback lost')
                row = {'time': time.time(), 'joint_deg': angles, 'max_error_deg': max(map(abs, angles))}
                log.write(json.dumps(row)+'\n'); log.flush()
                if time.monotonic()-last_print > 1:
                    print(json.dumps(row), flush=True)
                    last_print = time.monotonic()
                stable = stable+1 if row['max_error_deg'] <= .5 else 0
                if stable >= 10:
                    report.update(reached=True, final_deg=angles, completed_at=time.time())
                    print('ZERO REACHED', json.dumps(report), flush=True)
                    return
                time.sleep(.1)
        raise RuntimeError('Zero arrival timeout after 90 seconds')
    except BaseException as exc:
        report['error'] = repr(exc)
        from inference_client.fault_stop import latch_software_stop
        report['electronic_stop_sent'] = False
        try:
            latch_software_stop(stop_path)
            report['software_stop_latched'] = True
        except Exception as stop_error:
            report['stop_error'] = repr(stop_error)
        print('Zero move aborted; no further targets or automatic electronic stop/reset/disable. '
              'An accepted target may still execute; position hold is not guaranteed.', flush=True)
        raise
    finally:
        try:
            (args.output/'result.json').write_text(json.dumps(report, indent=2))
        finally:
            arm.disconnect()
            for s, handler in old.items():
                signal.signal(s, handler)


if __name__ == '__main__':
    main()
