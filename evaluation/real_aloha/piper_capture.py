"""Read-only, timestamp-checked ROS + Piper feedback recording. Never enables arms."""
import argparse
import json
import threading
import time
from pathlib import Path
import numpy as np
from .piper_contract import CAMERAS, PiperContract, check_timestamps, finite


def collect_commands(path, start, end):
    """Only explicitly timestamped SEND ATTEMPTS; feedback is never an action label."""
    if path is None:
        return [], []
    rows, errors = [], []
    for line_no, line in enumerate(Path(path).read_text().splitlines(), 1):
        try:
            row = json.loads(line)
            if not row.get('sent'):
                continue
            stamp = float(row['sent_at'])
            if not np.isfinite(stamp):
                raise ValueError('nonfinite sent_at')
            if not start <= stamp <= end:
                continue
            finite(row['target_flange'], (2, 6), 'target_flange')
            if row.get('grippers_m') is not None:
                finite(row['grippers_m'], (2,), 'grippers_m')
            rows.append(row)
        except (ValueError, KeyError, TypeError) as exc:
            errors.append({'line': line_no, 'error': str(exc)})
    return rows, errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--seconds', type=float, default=10)
    parser.add_argument('--command-log', type=Path,
                        help='Timestamped eef_commands.jsonl from instrumented project client')
    args = parser.parse_args()
    if not np.isfinite(args.seconds) or args.seconds <= 0:
        parser.error('--seconds must be positive and finite')
    contract = PiperContract.load(args.profile)
    cfg = contract.config
    # Local imports keep offline tooling usable without ROS or the hardware SDK.
    import cv2
    import rospy
    from sensor_msgs.msg import Image
    from cv_bridge import CvBridge
    from pyAgxArm import create_agx_arm_config, AgxArmFactory, ArmModel
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output / 'profile.json').write_text(json.dumps(cfg, indent=2))
    report = {'profile_sha256': contract.digest, 'physical_commands': 0,
              'camera_clock': 'ROS header epoch seconds', 'feedback_clock': 'SDK host epoch seconds',
              'note': 'Bounded timestamp skew; not hardware-triggered synchronization.',
              'samples': 0, 'rejected_samples': 0, 'commands': 0, 'complete': False}
    arms, grippers, subs = [], [], []
    latest, lock, bridge = {}, threading.Lock(), CvBridge()
    started = time.time()
    previous = {}
    def callback(msg, key):
        try:
            frame = bridge.imgmsg_to_cv2(msg, 'rgb8')
            with lock:
                latest[key] = (frame.copy(), float(msg.header.stamp.to_sec()), int(msg.header.seq))
        except Exception as exc:
            report['last_camera_error'] = str(exc)
    try:
        rospy.init_node('zero_wam_piper_capture', anonymous=True, disable_signals=True)
        for key in CAMERAS:
            subs.append(rospy.Subscriber(cfg['cameras'][key], Image, callback,
                         callback_args=key, queue_size=1, buff_size=2**24))
        for channel in cfg['can_channels']:
            arm = AgxArmFactory.create_arm(create_agx_arm_config(
                robot=ArmModel.PIPER, firmeware_version=cfg['firmware'], channel=channel))
            arms.append(arm)
            grippers.append(arm.init_effector(arm.OPTIONS.EFFECTOR.AGX_GRIPPER))
            arm.connect()
        startup_deadline = time.monotonic() + cfg['startup_timeout_s']
        finish = None
        with (args.output / 'samples.jsonl').open('w') as stream:
            while finish is None or time.monotonic() < finish:
                tick = time.monotonic()
                try:
                    with lock:
                        images = {k: (v[0].copy(), v[1], v[2]) for k, v in latest.items()}
                    if set(images) != set(CAMERAS):
                        raise ValueError('Waiting for all three cameras')
                    stamps = {k: images[k][1] for k in CAMERAS}
                    poses, joints, openings, statuses = [], [], [], []
                    for i, (arm, grip) in enumerate(zip(arms, grippers)):
                        feedback = {'flange': arm.get_flange_pose(), 'joints': arm.get_joint_angles(),
                                    'gripper': grip.get_gripper_status(), 'status': arm.get_arm_status()}
                        if any(v is None for v in feedback.values()):
                            raise ValueError(f'Missing arm {i} feedback')
                        for k, v in feedback.items():
                            stamps[f'arm{i}.{k}'] = float(v.timestamp)
                        poses.append(list(feedback['flange'].msg))
                        joints.append(list(feedback['joints'].msg))
                        openings.append(float(feedback['gripper'].msg.value))
                        statuses.append(int(feedback['status'].msg.arm_status))
                    finite(poses, (2, 6), 'flange');finite(joints, (2, 6), 'joints')
                    finite(openings, (2,), 'grippers')
                    now = time.time()
                    skew = check_timestamps(stamps, now, previous, cfg['max_age_s'], cfg['max_skew_s'])
                    frame_dir = args.output / f'frame_{report["samples"]:06d}'
                    frame_dir.mkdir()
                    for key in CAMERAS:
                        if not cv2.imwrite(str(frame_dir / (key + '.png')), cv2.cvtColor(images[key][0], cv2.COLOR_RGB2BGR)):
                            raise IOError('Could not save camera frame')
                    row = {'index': report['samples'], 'time': now, 'timestamps': stamps,
                           'camera_seq': {k: images[k][2] for k in CAMERAS}, 'skew_s': skew,
                           'flange_m_rad': poses, 'joints_rad': joints, 'grippers_m': openings,
                           'arm_status': statuses, 'image_directory': frame_dir.name}
                    stream.write(json.dumps(row, allow_nan=False) + '\n');stream.flush()
                    previous = stamps
                    report['samples'] += 1
                    report.setdefault('first_sample_at', now)
                    report['last_sample_at'] = now
                    if finish is None:
                        finish = time.monotonic() + args.seconds
                except ValueError as exc:
                    report['rejected_samples'] += 1
                    report['last_rejection'] = str(exc)
                    if finish is None and time.monotonic() > startup_deadline:
                        raise RuntimeError('No synchronized sample before startup deadline: ' + str(exc))
                time.sleep(max(0, 1/cfg['sample_hz'] - (time.monotonic()-tick)))
        report['complete'] = True
    except BaseException as exc:
        report['error'] = f'{type(exc).__name__}: {exc}'
        raise
    finally:
        ended = time.time()
        for sub in subs:
            sub.unregister()
        for arm in arms:
            try:
                arm.disconnect()
            except Exception as exc:
                report.setdefault('disconnect_errors', []).append(str(exc))
        try:
            commands, errors = collect_commands(args.command_log,
                report.get('first_sample_at', ended), report.get('last_sample_at', ended))
            (args.output / 'commands.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in commands))
            report.update(commands=len(commands), command_log_errors=errors,
                          command_log=None if args.command_log is None else str(args.command_log))
        except Exception as exc:
            report['command_log_error'] = str(exc)
        report.update(started_at=started, ended_at=ended,
                      control_targets_available=bool(report['commands']))
        (args.output / 'capture.json').write_text(json.dumps(report, indent=2) + '\n')
        print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
