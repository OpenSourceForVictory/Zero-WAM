#!/usr/bin/env python3
"""Convert ALOHA HDF5 to LeRobot v3.0 with the official LeRobot writer.

Run with /home/agilex/miniconda3/envs/lerobot/bin/python.
State uses observations/qpos. By default action preserves the existing v2.1
converter's 28-value representation (poses, joints, grippers derived from
state). Use --action-layout raw for original HDF5 command actions instead.
No upload or source-file modification occurs.
"""
import argparse
import json
import re
import shutil
import tempfile
from pathlib import Path

import h5py
import numpy as np
from lerobot.datasets.lerobot_dataset import CODEBASE_VERSION, LeRobotDataset


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input_dir', required=True)
    parser.add_argument('--output_dir', required=True)
    parser.add_argument('--fps', type=int, default=30)
    parser.add_argument('--task', default=None)
    parser.add_argument('--prompt', default=None)
    parser.add_argument('--prompts_file', help='JSON mapping output episode index to task description')
    parser.add_argument('--robot_type', default='aloha_mobile')
    parser.add_argument('--video_codec', default='h264')
    parser.add_argument('--action-layout', choices=('legacy28', 'raw'), default='legacy28')
    parser.add_argument('--image-color-order', choices=('rgb', 'bgr'), default='rgb')
    return parser.parse_args()


def vector_names(size):
    if size == 14:
        return ([f'left_joint_{i}' for i in range(6)] + ['left_gripper'] +
                [f'right_joint_{i}' for i in range(6)] + ['right_gripper'])
    return [f'value_{i}' for i in range(size)]


def inspect_episode(path):
    with h5py.File(path, 'r') as file:
        required = ('observations/qpos', 'action', 'observations/images')
        if any(key not in file for key in required):
            raise ValueError(f'{path.name}: missing state, action or images')
        length = len(file['action'])
        if length == 0:
            raise ValueError(f'{path.name}: empty episode')
        features = {}
        for source, target in [('observations/qpos', 'observation.state'), ('action', 'action'),
                               ('observations/qvel', 'observation.velocity'),
                               ('observations/effort', 'observation.effort'), ('base_action', 'base_action')]:
            if source not in file:
                continue
            ds = file[source]
            if ds.ndim != 2 or ds.shape[0] != length or ds.shape[1] == 0:
                raise ValueError(f'{path.name}: invalid shape for {source}: {ds.shape}')
            features[target] = {'dtype': 'float32', 'shape': (ds.shape[1],), 'names': vector_names(ds.shape[1])}
        cameras = sorted(file['observations/images'].keys())
        if not cameras:
            raise ValueError(f'{path.name}: no cameras')
        for camera in cameras:
            ds = file[f'observations/images/{camera}']
            if ds.ndim != 4 or ds.shape[0] != length or ds.shape[-1] != 3 or ds.dtype != np.uint8:
                raise ValueError(f'{path.name}: {camera} must be uncompressed uint8 RGB/BGR frames (T,H,W,3)')
            if ds.shape[1] % 2 or ds.shape[2] % 2:
                raise ValueError(f'{path.name}: {camera} height and width must be even for video encoding')
            features[f'observation.images.{camera}'] = {
                'dtype': 'video', 'shape': tuple(ds.shape[1:]), 'names': ['height', 'width', 'channels']}
        if 'observations/images_depth' in file:
            raise ValueError(f'{path.name}: depth images are not supported by this RGB converter')
    return length, features


LEGACY_ACTION_NAMES = (
    [f'{side}_{name}' for side in ('left', 'right') for name in ('x', 'y', 'z', 'qx', 'qy', 'qz', 'qw')] +
    [f'{side}_joint_{i}' for side in ('left', 'right') for i in range(6)] + ['left_gripper', 'right_gripper'])


def legacy_actions(file):
    # Reuse exactly the existing converter's pose units and quaternion convention.
    from convert_to_lerobot_v21 import C_PiperForwardKinematics, compute_eff_pose, pose6d_to_quat_pose
    qpos = file['observations/qpos'][:].astype(np.float32)
    poses = {}
    for side in ('left', 'right'):
        key = f'observations/{side}_eff_pose'
        if key in file:
            pose = file[key][:].astype(np.float32)
            if pose.shape not in ((len(qpos), 6), (len(qpos), 7)):
                raise ValueError(f'{key}: expected (T,6) or (T,7) pose')
            poses[side] = pose6d_to_quat_pose(pose) if pose.shape[-1] == 6 else pose
    if len(poses) != 2:
        left, right = compute_eff_pose(qpos, C_PiperForwardKinematics())
        poses.setdefault('left', left)
        poses.setdefault('right', right)
    return np.concatenate([poses['left'], poses['right'], qpos[:, :6], qpos[:, 7:13],
                           qpos[:, 6:7], qpos[:, 13:14]], axis=-1).astype(np.float32)


def convert(args):
    if CODEBASE_VERSION != 'v3.0':
        raise RuntimeError('This converter requires a LeRobot environment supporting v3.0')
    if not 1 <= args.fps <= 240:
        raise ValueError('fps must be between 1 and 240')
    source, output = Path(args.input_dir).resolve(), Path(args.output_dir).resolve()
    if output == source or source in output.parents or output in source.parents:
        raise ValueError('Input and output must be separate directories, not nested')
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise FileExistsError(f'Output directory is not empty: {output}')
    files = sorted(source.rglob('*.hdf5'), key=lambda p: (
        int(re.search(r'\d+', p.stem).group()) if re.search(r'\d+', p.stem) else -1, str(p)))
    if not files:
        raise ValueError(f'No HDF5 episodes in {source}')
    prompts = None
    if args.prompts_file:
        mapping = json.loads(Path(args.prompts_file).read_text())
        if not isinstance(mapping, dict):
            raise ValueError('prompts_file must contain a JSON object')
        prompts = [mapping.get(str(i)) for i in range(len(files))]
    else:
        prompts = [args.prompt or args.task or 'Manipulation task'] * len(files)
    if any(not isinstance(prompt, str) or not prompt.strip() for prompt in prompts):
        raise ValueError('Every episode must have a nonempty task description')
    lengths, features = [], None
    for path in files:
        length, schema = inspect_episode(path)
        if features is not None and features != schema:
            raise ValueError(f'{path.name}: cameras or vector dimensions differ between episodes')
        features = schema
        if args.action_layout == 'legacy28' and features['observation.state']['shape'] != (14,):
            raise ValueError(f'{path.name}: legacy28 requires a 14-value qpos vector')
        lengths.append(length)
    if args.action_layout == 'legacy28':
        features['action'] = {'dtype': 'float32', 'shape': (28,), 'names': LEGACY_ACTION_NAMES}
    print(f'Validated {len(files)} episodes, {sum(lengths)} frames; LeRobot v3.0', flush=True)
    action_source = 'state-derived poses/joints/grippers (v2.1 compatible)' if args.action_layout == 'legacy28' else 'HDF5 action'
    print(f'observation.state = HDF5 observations/qpos; action = {action_source}', flush=True)
    output.mkdir(parents=True, exist_ok=True)
    # Keep incomplete datasets out of the web browser's dataset list.
    with tempfile.TemporaryDirectory(prefix='.v30-build-', dir=output) as work:
        build = Path(work) / 'dataset'
        dataset = LeRobotDataset.create(repo_id='local/aloha-converted', root=build,
                                        fps=args.fps, robot_type=args.robot_type,
                                        features=features, vcodec=args.video_codec,
                                        image_writer_threads=2, video_backend='pyav')
        try:
            for episode, (path, length, prompt) in enumerate(zip(files, lengths, prompts)):
                print(f'[{episode + 1}/{len(files)}] {path.name}: {length} frames', flush=True)
                with h5py.File(path, 'r') as file:
                    actions = legacy_actions(file) if args.action_layout == 'legacy28' else None
                    vectors = {'observation.state': 'observations/qpos', 'action': 'action',
                               'observation.velocity': 'observations/qvel',
                               'observation.effort': 'observations/effort', 'base_action': 'base_action'}
                    for index in range(length):
                        frame = {'task': prompt.strip()}
                        for target, key in vectors.items():
                            if target in features:
                                values = actions[index] if target == 'action' and actions is not None else np.asarray(file[key][index], dtype=np.float32)
                                if not np.isfinite(values).all():
                                    raise ValueError(f'{path.name}: {key} frame {index} has nonfinite values')
                                frame[target] = values
                        for camera in file['observations/images']:
                            image = np.asarray(file[f'observations/images/{camera}'][index])
                            if args.image_color_order == 'bgr':
                                image = image[..., ::-1].copy()
                            frame[f'observation.images.{camera}'] = image
                        dataset.add_frame(frame)
                        if (index + 1) % 100 == 0:
                            print(f'  {index + 1}/{length} frames', flush=True)
                dataset.save_episode()
                print(f'  episode {episode} saved', flush=True)
        finally:
            dataset.finalize()
            dataset.stop_image_writer()
        info = json.loads((build / 'meta/info.json').read_text())
        if info['total_episodes'] != len(files) or info['total_frames'] != sum(lengths):
            raise RuntimeError('Converted metadata does not match source episodes')
        (build / 'meta/source_episodes.json').write_text(json.dumps({
            'action_source': action_source, 'action_layout': args.action_layout, 'state_source': 'observations/qpos',
            'image_color_order': args.image_color_order,
            'episodes': [{'episode_index': i, 'source': str(path.relative_to(source)), 'length': length}
                         for i, (path, length) in enumerate(zip(files, lengths))]}, indent=2))
        for child in build.iterdir():
            if child.name != 'meta':
                shutil.move(str(child), str(output / child.name))
        shutil.move(str(build / 'meta'), str(output / 'meta'))
    print(f'Done -> {output}\n  {len(files)} episodes, {sum(lengths)} frames, format v3.0', flush=True)


if __name__ == '__main__':
    convert(parse_args())
