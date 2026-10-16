"""Piper action layouts. Units are preserved; unused 55D slots are not labels."""
import json
from pathlib import Path

import numpy as np

LEGACY_ACTION_NAMES = (
    [f'{side}_{name}' for side in ('left', 'right')
     for name in ('x', 'y', 'z', 'qx', 'qy', 'qz', 'qw')] +
    [f'{side}_joint_{i}' for side in ('left', 'right') for i in range(6)] +
    ['left_gripper', 'right_gripper'])

STANDARD55_ACTION_NAMES = (
    [f'left_joint_{i}' for i in range(6)] + ['left_joint_6_unused'] +
    [f'right_joint_{i}' for i in range(6)] + ['right_joint_6_unused'] +
    [f'{side}_{name}' for side in ('left', 'right')
     for name in ('x', 'y', 'z', 'qx', 'qy', 'qz', 'qw')] +
    ['left_gripper', 'right_gripper'] +
    [f'{part}_slot_{i}_unused' for part, count in
     [('left_hand', 6), ('right_hand', 6), ('waist', 4), ('head', 2),
      ('mobile_base', 3), ('reserved', 4)] for i in range(count)])

MAPPING_28_TO_55 = [
    ([0, 6], [14, 20]), ([7, 13], [20, 26]),
    ([14, 21], [0, 7]), ([21, 28], [7, 14]), ([28, 30], [26, 28]),
]


def action28_to_55(action):
    src = np.asarray(action, dtype=np.float32)
    if src.ndim < 1 or src.shape[-1] != 28:
        raise ValueError('action 最后一维必须为 28')
    dst = np.zeros((*src.shape[:-1], 55), dtype=np.float32)
    mask = np.zeros(55, dtype=bool)
    for (start, end), (source_start, source_end) in MAPPING_28_TO_55:
        dst[..., start:end] = src[..., source_start:source_end]
        mask[start:end] = True
    return dst, mask


def write_standard55_metadata(meta_dir):
    """Persist one dataset-wide loss mask and layout/units beside official metadata."""
    meta_dir = Path(meta_dir)
    _, mask = action28_to_55(np.zeros(28, dtype=np.float32))
    units = [None] * 55
    for start, end in ((0, 6), (7, 13)):
        units[start:end] = ['rad'] * 6
    for start in (14, 21):
        units[start:start + 7] = ['mm'] * 3 + ['unitless'] * 4
    units[28:30] = ['m', 'm']
    metadata = {
        'layout': 'standard55', 'layout_version': 'piper_6_joint_v1',
        'dtype': 'float32', 'shape': [55], 'index_base': 0,
        'slice_convention': 'Python half-open [start:end]',
        'names': STANDARD55_ACTION_NAMES, 'units': units,
        'quaternion_order': 'xyzw', 'unit_conversion': 'none; position remains mm',
        'action_source': 'puppet observations/qpos; recorded end poses or FK of puppet joints',
        'state_source': 'observations/qpos; never remapped with the action mapping',
        'mapping_from_28': [{'target': target, 'source': source}
                            for target, source in MAPPING_28_TO_55],
        'valid_mask': mask.tolist(), 'valid_count': int(mask.sum()),
        'invalid_fill': 0.0, 'mask_shape': [55], 'mask_dtype': 'bool',
        'mask_scope': 'dataset-wide; broadcast over frames and batches',
        'training_requirement': 'Load action_mask.npy or action_mask.json and exclude false slots '
                                'from the loss. Zero padding is not a ground-truth action. '
                                'Saving the mask does not automatically apply it in a trainer.',
        'joint_convention': 'joint_0 through joint_5 mean physical J1 through J6; '
                            'slot 6 on each side is unused, not a kinematic mapping to a 7-joint arm',
        'undefined_slots': 'Hand, waist, head, mobile-base and reserved components, '
                           'coordinate frames and units are undefined and invalid.',
        'applicable_datasets': ['261014_data_93episode', '261015_box_pick_80episode'],
        'stats_note': 'Action statistics are recomputed from float32 55D output; '
                      'padded-slot statistics describe padding only and must be masked in training.',
    }
    np.save(meta_dir / 'action_mask.npy', mask, allow_pickle=False)
    (meta_dir / 'action_mask.json').write_text(json.dumps(mask.tolist(), indent=2))
    (meta_dir / 'action_layout.json').write_text(json.dumps(metadata, indent=2, ensure_ascii=False))
    info_path = meta_dir / 'info.json'
    info = json.loads(info_path.read_text())
    info.update(action_layout='standard55', action_layout_metadata='meta/action_layout.json',
                action_valid_mask='meta/action_mask.npy')
    info_path.write_text(json.dumps(info, indent=2, ensure_ascii=False))
