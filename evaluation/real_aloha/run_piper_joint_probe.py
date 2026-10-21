"""Bounded, supervised MOVE_J probe of an absolute 28D ALOHA prediction.

Default is read-only. No EEF inverse kinematics, reset, or gripper commands.
This checks joint bounds and feedback, not scene collision geometry.
"""
import argparse
import json
import signal
from pathlib import Path
import time

import numpy as np


def joint_targets(action):
    action = np.asarray(action)
    if action.shape != (28, 2, 16) or not np.isfinite(action).all():
        raise ValueError('Expected finite absolute action (28,2,16)')
    return action[14:26, 1, :].T.reshape(16, 2, 6).copy()


def check_targets(targets, current, limits):
    targets, current, limits = map(np.asarray, (targets, current, limits))
    if (targets.ndim != 3 or targets.shape[1:] != (2, 6) or len(targets) == 0
            or current.shape != (2, 6) or limits.shape != (2, 6, 2)
            or not all(np.isfinite(v).all() for v in (targets, current, limits))):
        raise ValueError('Invalid joint feedback/limits/targets')
    if np.any(limits[..., 0] >= limits[..., 1]):
        raise ValueError('Invalid controller limits')
    if np.any(current < limits[..., 0] - np.deg2rad(.2)) or np.any(current > limits[..., 1] + np.deg2rad(.2)):
        raise ValueError('Measured joints outside controller limits')
    # Near-boundary starts can move inward under joint control. Do not require
    # the EEF planner's Jacobian condition; do require an interior target.
    margin = np.deg2rad(.5)
    if np.any(targets < limits[..., 0] + margin) or np.any(targets > limits[..., 1] - margin):
        raise ValueError('Predicted joint target outside controller limits/margin')
    first = float(np.rad2deg(np.abs(targets[0] - current)).max())
    if len(targets) > 1 and np.rad2deg(np.abs(np.diff(targets, axis=0))).max() > 3:
        raise ValueError('Adjacent target jump > 3 deg')
    if np.rad2deg(np.abs(targets - current)).max() > 10:
        raise ValueError('Probe excursion > 10 deg')
    return {'first_max_joint_change_deg': first}


def plan_approach(current, target, limits):
    """Joint-linear preparation path, <=30 deg total and <=1 deg per segment.

    This is an explicit supervised preparation move, not a model-timed rollout.
    A zero/near-boundary start may move inward; the endpoint needs 0.5 deg margin.
    """
    current, target, limits = map(lambda x: np.asarray(x, dtype=float), (current, target, limits))
    if current.shape != (2,6) or target.shape != (2,6) or limits.shape != (2,6,2):
        raise ValueError('Invalid approach shapes')
    if not all(np.isfinite(x).all() for x in (current,target,limits)) or np.any(limits[...,0]>=limits[...,1]):
        raise ValueError('Invalid approach values')
    if np.any(current<limits[...,0]) or np.any(current>limits[...,1]):
        raise ValueError('Approach start outside controller limits')
    margin=np.deg2rad(.5)
    if np.any(target<limits[...,0]+margin) or np.any(target>limits[...,1]-margin):
        raise ValueError('Approach target outside controller limits/margin')
    span=float(np.rad2deg(abs(target-current)).max())
    if span>30:
        raise ValueError(f'Approach exceeds 30 deg total: {span:.3f}')
    count=max(1,int(np.ceil(span)))
    path=np.asarray([current+(target-current)*(i/count) for i in range(1,count+1)])
    return path


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--prediction', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--execute', action='store_true')
    p.add_argument('--max-targets', type=int, default=1)
    p.add_argument('--approach-first-target', action='store_true',
                   help='Supervised preparation: <=30 deg total, <=1 deg segments, 5 percent speed; grippers held')
    p.add_argument('--stop-file', type=Path, default=Path('/home/agilex/models/lingbot-va-piper/client/runtime_config.json'))
    args = p.parse_args()
    if not 1 <= args.max_targets <= 16:
        p.error('max-targets must be in [1,16]')
    if args.approach_first_target and args.max_targets != 1:
        p.error('Approach is restricted to the first target')
    args.output.mkdir(parents=True, exist_ok=False)
    report = {'joint_commands': 0, 'gripper_commands': 0, 'execute_requested': args.execute,
              'mode': 'absolute_joint_rad', 'ready': False, 'collision_checked': False}
    arms = []
    armed = False
    def interrupted(signum, frame):
        raise RuntimeError(f'Interrupted by signal {signum}')
    signal.signal(signal.SIGTERM, interrupted)
    def save():
        (args.output/'report.json').write_text(json.dumps(report, indent=2)+'\n')
    def check_stop():
        if json.loads(args.stop_file.read_text()).get('emergency_stop', True):
            raise RuntimeError('Operator emergency_stop is set; no reset attempted')
    def read():
        q = []
        for arm in arms:
            state, angles = arm.get_arm_status(), arm.get_joint_angles()
            if any(x is None or not -.1 <= time.time()-x.timestamp <= .5 for x in (state, angles)):
                raise RuntimeError('Missing/stale controller feedback')
            if int(state.msg.arm_status) != 0 or int(state.msg.ctrl_mode) != 1:
                raise RuntimeError('Controller not in healthy CAN control mode')
            q.append(list(angles.msg))
        q = np.asarray(q)
        if q.shape != (2, 6) or not np.isfinite(q).all():
            raise RuntimeError('Invalid joint feedback')
        return q
    try:
        provenance = json.loads((args.prediction/'report.json').read_text())
        if not provenance.get('completed') or provenance['reset'].get('action_contract') != 'aloha_absolute_28_continuous_v2':
            raise ValueError('Prediction is not a completed absolute ALOHA v2 inference')
        if not provenance['reset'].get('checkpoint_contract_bound'):
            raise ValueError('Checkpoint contract not bound')
        targets = joint_targets(np.load(args.prediction/'raw_action.npy', allow_pickle=False))[:args.max_targets]
        from pyAgxArm import create_agx_arm_config, AgxArmFactory, ArmModel
        for channel in ('can_left', 'can_right'):
            arm = AgxArmFactory.create_arm(create_agx_arm_config(robot=ArmModel.PIPER, firmeware_version='v188', channel=channel))
            arms.append(arm)
            arm.connect()
            time.sleep(.3)
        limits = []
        for arm in arms:
            row = []
            for i in range(1, 7):
                sample = arm.get_joint_angle_vel_limits(i, timeout=1)
                if sample is None:
                    raise RuntimeError('Missing controller joint limits')
                row.append([sample.msg.min_angle_limit, sample.msg.max_angle_limit])
            limits.append(row)
        current = read()
        report.update(current_joint_deg=np.rad2deg(current).tolist(),
                      first_target_joint_deg=np.rad2deg(targets[0]).tolist(),
                      first_delta_joint_deg=np.rad2deg(targets[0]-current).tolist(), limits_rad=limits)
        if args.approach_first_target:
            targets=plan_approach(current,targets[0],limits)
            report.update(approach=True,planned_segments=len(targets),speed_percent=5,
                          segment_max_deg=1,trajectory_joint_deg=np.rad2deg(targets).tolist())
        else:
            report.update(check_targets(targets, current, limits))
        check_stop()
        report['ready'] = True
        save()
        if args.execute:
            if not 0 <= time.time()-provenance['finished_at'] <= 120:
                raise RuntimeError('Prediction older than 120 seconds; recapture/reinfer')
            armed = True
            for arm in arms:
                arm.set_auto_set_motion_mode_enabled(True)
                arm.set_speed_percent(5)
                for _ in range(100):
                    check_stop()
                    if arm.enable():
                        break
                    time.sleep(.02)
                else:
                    raise RuntimeError('Enable timed out')
            if np.max(np.abs(read()-current)) > np.deg2rad(.2):
                raise RuntimeError('Start posture moved since planning')
            if not args.approach_first_target:
                check_targets(targets, read(), limits)
            previous=current.copy()
            report['segments']=[]
            for index,target in enumerate(targets):
                check_stop()
                measured=read()
                if args.approach_first_target:
                    if (np.max(abs(measured-previous))>np.deg2rad(.3)
                            or np.max(abs(target-measured))>np.deg2rad(1.3)):
                        raise RuntimeError('Approach tracking/segment bound exceeded')
                else:
                    check_targets(target[None], measured, limits)
                for arm, q in zip(arms, target):
                    check_stop()
                    arm.move_j(q.tolist())
                    report['joint_commands'] += 1
                    save()
                deadline = time.monotonic()+5
                settled=0
                while True:
                    check_stop()
                    actual = read()
                    if args.approach_first_target and np.max(abs(actual-target))>np.deg2rad(2):
                        raise RuntimeError('Unexpected joint motion during approach')
                    tol=.15 if args.approach_first_target else 1
                    settled=settled+1 if np.max(np.abs(actual-target))<=np.deg2rad(tol) else 0
                    if settled>=3:
                        break
                    if time.monotonic() > deadline:
                        raise RuntimeError('Joint target tracking timed out')
                    time.sleep(.02)
                report['last_measured_joint_deg'] = np.rad2deg(actual).tolist()
                report['segments'].append({'index':index,'target_deg':np.rad2deg(target).tolist(),
                                           'measured_deg':np.rad2deg(actual).tolist(), 'time':time.time()})
                previous=target.copy()
                save()
                print(f'Segment {index+1}/{len(targets)} reached',flush=True)
            report['completed'] = True
    except BaseException as exc:
        report['error'] = f'{type(exc).__name__}: {exc}'
        if armed:
            report['emergency_stop_errors'] = []
            for arm in arms:
                try:
                    arm.electronic_emergency_stop()
                except Exception as stop_exc:
                    report['emergency_stop_errors'].append(str(stop_exc))
        raise
    finally:
        save()
        for arm in arms:
            arm.disconnect()
        print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
