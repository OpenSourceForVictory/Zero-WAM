"""Descriptive action diagnostics; these are not task-success or safety scores."""
import numpy as np


def summarize_action(action, first_chunk=False, current_joints=None, quantiles=None):
    a = np.asarray(action)
    result = {'shape': list(a.shape), 'finite': bool(np.isfinite(a).all())}
    if a.ndim != 3 or a.shape[0] not in (14, 28) or not result['finite']:
        return result
    seq = a.reshape(a.shape[0], -1).T
    seq = seq[a.shape[2]:] if first_chunk else seq
    result['usable_steps'] = len(seq)
    if not len(seq):
        return result
    joint_ids = list(range(14, 26)) if a.shape[0] == 28 else list(range(6)) + list(range(7, 13))
    grip_ids = [26, 27] if a.shape[0] == 28 else [6, 13]
    joints = np.rad2deg(seq[:, joint_ids])
    result.update(joint_min_deg=joints.min(0).tolist(), joint_max_deg=joints.max(0).tolist(),
                  gripper_min_m=seq[:, grip_ids].min(0).tolist(), gripper_max_m=seq[:, grip_ids].max(0).tolist())
    if len(seq) > 1:
        delta = np.abs(np.diff(joints, axis=0))
        result.update(joint_step_delta_max_deg=float(delta.max()), joint_step_delta_mean_deg=float(delta.mean()))
    if current_joints is not None:
        current = np.asarray(current_joints).reshape(-1)[:12]
        if len(current) == 12 and np.isfinite(current).all():
            result['first_target_minus_current_deg'] = (joints[0] - np.rad2deg(current)).tolist()
    if a.shape[0] == 28:
        result['quaternion_norm_mae'] = [float(np.abs(np.linalg.norm(seq[:, i:i+4], axis=1)-1).mean()) for i in (3, 10)]
        if quantiles is not None:
            ids = list(range(26)) + [28, 29]
            lo, hi = (np.asarray(quantiles[k])[ids] for k in ('q01', 'q99'))
            result['outside_training_q01_q99_fraction'] = float(((seq < lo) | (seq > hi)).mean())
    return result
