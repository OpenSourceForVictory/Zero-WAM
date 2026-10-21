"""Explicit physical Piper EEF contract; no SDK, controller or model imports."""
from pathlib import Path
import hashlib
import json
import numpy as np
from scipy.spatial.transform import Rotation
from .piper_eef import _transforms

CAMERAS = ('cam_high', 'cam_left_wrist', 'cam_right_wrist')
USED = list(range(7)) + [28] + list(range(7, 14)) + [29]


def finite(value, shape, name):
    value = np.asarray(value, dtype=float)
    if value.shape != shape or not np.isfinite(value).all():
        raise ValueError(f'{name}: expected finite {shape}')
    return value


class PiperContract:
    def __init__(self, config):
        self.config = json.loads(json.dumps(config, allow_nan=False))
        if config['schema_version'] != 1:
            raise ValueError('Unsupported profile version')
        if config['action_convention'] != 'episode_relative_physical_xyzw':
            raise ValueError('This contract requires physical xyzw, not robotwin_eval')
        if config['feedback_units'] != 'm_rad' or config['gripper_units'] != 'm':
            raise ValueError('Explicit m/rad feedback and metre grippers required')
        self.bases = _transforms(config['task_from_base'], 'task_from_base')
        self.tools = _transforms(config['flange_from_tcp'], 'flange_from_tcp')
        self.closed = finite(config['gripper_closed_m'], (2,), 'closed')
        self.open = finite(config['gripper_open_m'], (2,), 'open')
        if np.any(self.closed < 0) or np.any(self.open <= self.closed):
            raise ValueError('Invalid measured gripper interval')
        if set(config['cameras']) != set(CAMERAS) or len(set(config['cameras'].values())) != 3:
            raise ValueError('Three unique camera topics required')
        for key in ('sample_hz', 'max_age_s', 'max_skew_s', 'startup_timeout_s'):
            if not np.isfinite(config[key]) or config[key] <= 0:
                raise ValueError(f'Invalid {key}')
        if len(config['can_channels']) != 2 or len(set(config['can_channels'])) != 2:
            raise ValueError('Two distinct CAN channels required')
        self.digest = hashlib.sha256(json.dumps(self.config, sort_keys=True).encode()).hexdigest()

    @classmethod
    def load(cls, path):
        return cls(json.loads(Path(path).read_text()))

    def matrices(self, poses):
        poses = finite(poses, (2, 6), 'flange poses')
        flange = np.repeat(np.eye(4)[None], 2, axis=0)
        flange[:, :3, :3] = Rotation.from_euler('xyz', poses[:, 3:]).as_matrix()
        flange[:, :3, 3] = poses[:, :3]
        return self.bases @ flange @ self.tools

    def encode(self, initial_flange, target_flange, grippers):
        initial, target = self.matrices(initial_flange), self.matrices(target_flange)
        grippers = finite(grippers, (2,), 'grippers')
        if np.any(grippers < self.closed - 1e-6) or np.any(grippers > self.open + 1e-6):
            raise ValueError('Gripper outside configured range; not clipped')
        out = np.empty((2, 8))
        out[:, :3] = target[:, :3, 3] - initial[:, :3, 3]
        q = (Rotation.from_matrix(initial[:, :3, :3]).inv() *
             Rotation.from_matrix(target[:, :3, :3])).as_quat()
        q[q[:, 3] < 0] *= -1
        out[:, 3:7] = q
        out[:, 7] = (grippers - self.closed) / (self.open - self.closed)
        return out.reshape(16)

    def decode(self, initial_flange, action):
        action = finite(action, (16,), 'action').reshape(2, 8)
        norms = np.linalg.norm(action[:, 3:7], axis=1)
        if np.any(norms < 1e-8):
            raise ValueError('Zero quaternion')
        if np.any(action[:, 7] < -1e-9) or np.any(action[:, 7] > 1 + 1e-9):
            raise ValueError('Gripper fraction outside [0,1]; not clipped')
        initial = self.matrices(initial_flange)
        target = initial.copy()
        target[:, :3, 3] += action[:, :3]
        target[:, :3, :3] = (Rotation.from_matrix(initial[:, :3, :3]) *
                             Rotation.from_quat(action[:, 3:7])).as_matrix()
        flange = np.linalg.inv(self.bases) @ target @ np.linalg.inv(self.tools)
        poses = np.c_[flange[:, :3, 3], Rotation.from_matrix(flange[:, :3, :3]).as_euler('xyz')]
        return poses, self.closed + action[:, 7] * (self.open - self.closed)


class ActionQuantiles:
    """Explicit candidate stats: numerical reversibility is not deployment validation."""
    def __init__(self, q01, q99, provenance):
        self.q01 = finite(q01, (16,), 'q01')
        self.q99 = finite(q99, (16,), 'q99')
        if np.any(self.q99 < self.q01):
            raise ValueError('q99 must not be below q01')
        self.scale = self.q99 - self.q01 + 1e-6
        self.provenance = provenance

    @classmethod
    def load(cls, path):
        path = Path(path)
        raw = path.read_bytes()
        data = json.loads(raw)
        if data.get('method') != 'abs':
            raise ValueError('Expected released method=abs stats, explicitly selected')
        stats = data['norm_stats']
        q = {}
        for name in ('q01', 'q99'):
            hand = finite(stats['action.hand.position'][name], (14,), name)
            grip = finite(stats['action.effector.position'][name], (2,), name)
            q[name] = np.r_[hand[:7], grip[0], hand[7:], grip[1]]
        return cls(q['q01'], q['q99'], {'path': str(path.resolve()),
                   'sha256': hashlib.sha256(raw).hexdigest(), 'deployment_validated': False})

    def normalize(self, actions):
        a = np.asarray(actions, float)
        if a.shape[-1:] != (16,) or not np.isfinite(a).all():
            raise ValueError('Expected finite (...,16) actions')
        return (a - self.q01) / self.scale * 2 - 1

    def denormalize(self, actions):
        a = np.asarray(actions, float)
        if a.shape[-1:] != (16,) or not np.isfinite(a).all():
            raise ValueError('Expected finite (...,16) actions')
        return (a + 1) / 2 * self.scale + self.q01


def check_timestamps(stamps, now, previous, max_age, max_skew):
    """No arrival-time substitution: every camera/feedback source needs its stamp."""
    values = np.asarray(list(stamps.values()), float)
    if not len(values) or not np.isfinite(values).all():
        raise ValueError('Missing/invalid timestamps')
    ages = now - values
    if np.any(ages < -.02) or np.any(ages > max_age):
        raise ValueError('Stale or future-dated input')
    if np.ptp(values) > max_skew:
        raise ValueError('Camera/robot timestamp skew exceeds profile')
    if any(t <= previous.get(k, -np.inf) for k, t in stamps.items()):
        raise ValueError('Repeated or regressing source timestamp')
    return float(np.ptp(values))
