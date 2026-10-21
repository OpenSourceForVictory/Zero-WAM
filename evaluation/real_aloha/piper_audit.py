"""Audit captured feedback and explicit command labels without loading a robot SDK."""
import argparse
import json
from pathlib import Path
import numpy as np
from scipy.spatial.transform import Rotation
from .piper_contract import PiperContract, ActionQuantiles, CAMERAS, check_timestamps, finite


def audit(directory, stats_path=None):
    p = Path(directory)
    contract = PiperContract.load(p / 'profile.json')
    capture = json.loads((p / 'capture.json').read_text())
    if capture['profile_sha256'] != contract.digest:
        raise ValueError('Capture profile digest mismatch')
    rows = [json.loads(line) for line in (p / 'samples.jsonl').read_text().splitlines()]
    if not rows:
        raise ValueError('No synchronized samples')
    previous = {}
    for r in rows:
        if set(r['timestamps']) != set(CAMERAS) | {f'arm{i}.{k}' for i in range(2) for k in ('flange','joints','gripper','status')}:
            raise ValueError('Incomplete source timestamps')
        check_timestamps(r['timestamps'], r['time'], previous,
                         contract.config['max_age_s'], contract.config['max_skew_s'])
        previous = r['timestamps']
        folder = (p / r['image_directory']).resolve()
        if not folder.is_relative_to(p.resolve()):
            raise ValueError('Image path outside capture')
        for camera in CAMERAS:
            from PIL import Image
            with Image.open(folder / (camera + '.png')) as image:
                image.verify()
    initial = np.asarray(rows[0]['flange_m_rad'])
    stats = ActionQuantiles.load(stats_path) if stats_path else None
    errors, actions = [], []
    for r in rows:
        a = contract.encode(initial, r['flange_m_rad'], r['grippers_m'])
        actions.append(a)
        recovered = stats.denormalize(stats.normalize(a)) if stats else a
        flange, grip = contract.decode(initial, recovered)
        original = np.asarray(r['flange_m_rad'])
        errors.append([np.linalg.norm(flange[:, :3]-original[:, :3], axis=1).max(),
                       (Rotation.from_euler('xyz', flange[:, 3:]).inv() *
                        Rotation.from_euler('xyz', original[:, 3:])).magnitude().max(),
                       np.max(np.abs(grip-r['grippers_m']))])
    errors = np.asarray(errors).max(axis=0)
    commands = [json.loads(line) for line in (p / 'commands.jsonl').read_text().splitlines()]
    command_rows = []
    times = np.array([r['time'] for r in rows])
    for cmd in commands:
        if not cmd.get('sent') or not np.isfinite(cmd['sent_at']):
            raise ValueError('Invalid send event')
        finite(cmd['target_flange'], (2, 6), 'command target')
        if not times[0] <= cmd['sent_at'] <= times[-1]:
            raise ValueError('Command outside measured episode')
        if 'completed_at' in cmd and (not np.isfinite(cmd['completed_at']) or cmd['completed_at'] < cmd['sent_at']):
            raise ValueError('Invalid command completion timestamp')
        # Keep missing gripper labels missing, never substitute feedback for training.
        record = {'sent_at': cmd['sent_at'], 'send_attempt_only': True,
                  'target_flange': cmd['target_flange'], 'grippers_m': cmd.get('grippers_m'),
                  'pose_reached': cmd.get('pose_reached', False)}
        if cmd.get('grippers_m') is not None:
            a = contract.encode(initial, cmd['target_flange'], cmd['grippers_m'])
            recovered = stats.denormalize(stats.normalize(a)) if stats else a
            flange, grip = contract.decode(initial, recovered)
            target = np.asarray(cmd['target_flange'])
            record['roundtrip_position_error_m'] = float(np.linalg.norm(flange[:, :3]-target[:, :3],axis=1).max())
            record['action_relative'] = a.tolist()
        index = np.searchsorted(times, cmd.get('completed_at', cmd['sent_at']))
        if index < len(rows) and times[index]-cmd.get('completed_at', cmd['sent_at']) <= contract.config['max_age_s']:
            actual = np.asarray(rows[index]['flange_m_rad'])
            target = np.asarray(cmd['target_flange'])
            record['post_completion_position_error_m'] = np.linalg.norm(actual[:, :3]-target[:, :3],axis=1).tolist()
        command_rows.append(record)
    xyz = np.asarray([r['flange_m_rad'] for r in rows])[:, :, :3]
    result = {'samples': len(rows), 'physical_commands_by_audit': 0,
              'capture_complete': capture['complete'], 'profile_sha256': contract.digest,
              'roundtrip_max_position_error_m': float(errors[0]),
              'roundtrip_max_rotation_error_rad': float(errors[1]),
              'roundtrip_max_gripper_error_m': float(errors[2]),
              'roundtrip_passed': bool(np.all(errors < 1e-8)),
              'normalization_checked': stats is not None,
              'normalization': None if stats is None else stats.provenance,
              'measured_range_from_start_m': np.linalg.norm(xyz-xyz[:1],axis=2).max(axis=0).tolist(),
              'commands': command_rows, 'control_targets_available': bool(command_rows),
              'nonzero_controller_status_samples': sum(any(r['arm_status']) for r in rows),
              'deployment_validated': False,
              'note': 'Roundtrip proves arithmetic consistency only, not calibration/model quality. Feedback is not a command label.'}
    np.save(p / 'measured_relative_actions.npy', np.asarray(actions))
    (p / 'audit.json').write_text(json.dumps(result, indent=2, allow_nan=False) + '\n')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    parser.add_argument('--stats', type=Path, help='Explicit candidate; never fitted from a stationary capture')
    args = parser.parse_args()
    result = audit(args.directory, args.stats)
    print(json.dumps(result, indent=2))
    if not result['roundtrip_passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
