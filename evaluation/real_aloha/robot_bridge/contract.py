"""Real-robot observation/action contract helpers.

Conversions and bounded-action validation shared by the real-robot server.
The wire contract matches the RoboDojo simulator session:
  - teacher_observation -> cam_high/cam_left_wrist/cam_right_wrist,
    states (14: left 6 joints + opening + right 6 joints + opening),
    eef_positions (2,3), eef_quaternions_wxyz (2,4), instruction, remaining_steps.
  - eef_joint_target -> 14-dim absolute joint action (bounded IK, server-side).
  - chunk_step -> execute 14-dim absolute joint action on the real arms.
"""

from __future__ import annotations

import numpy as np

CAMERAS = ("cam_high", "cam_left_wrist", "cam_right_wrist")
ACTION_DIM = 14
ARMS = ("left", "right")
GRIPPER_INDEX = {arm: 6 + i * 7 for i, arm in enumerate(ARMS)}  # left:6, right:13
MAX_DECISION_TRANSLATION_M = 0.05
MAX_DECISION_ROTATION_RAD = 0.35


def quat_wxyz_to_euler(quat_wxyz, convention="xyz"):
    """Unit wxyz quaternion -> XYZ extrinsic Euler angles (rad)."""
    from scipy.spatial.transform import Rotation

    q = np.asarray(quat_wxyz, float)
    if q.shape != (4,) or abs(np.linalg.norm(q) - 1.0) > 1e-3:
        raise ValueError("Expected unit wxyz quaternion")
    return Rotation.from_quat([q[1], q[2], q[3], q[0]]).as_euler(convention)


def states_to_pos_cmd(states, gripper_pairs=None):
    """Not used for joint execution; kept for EEF-path /pos_cmd consumers."""
    raise NotImplementedError("Real-robot execution is joint-space (14-dim).")


def bounded_decision_error(current, target):
    """Return (translation_m, rotation_rad) between current and target EEF pose."""
    from scipy.spatial.transform import Rotation

    cur_pos, cur_q = np.asarray(current["position"], float), np.asarray(
        current["quaternion_wxyz"], float
    )
    tgt_pos, tgt_q = np.asarray(target["position"], float), np.asarray(
        target["quaternion_wxyz"], float
    )
    if cur_pos.shape != (3,) or tgt_pos.shape != (3,):
        raise ValueError("EEF position must be 3-D")
    for p, q in ((cur_pos, cur_q), (tgt_pos, tgt_q)):
        if (
            q.shape != (4,)
            or not np.isfinite(np.r_[p, q]).all()
            or abs(np.linalg.norm(q) - 1) > 1e-3
        ):
            raise ValueError("Expected finite position and unit wxyz quaternion")
    if type(target.get("gripper_closed")) is not bool:
        raise ValueError("Explicit boolean gripper_closed required")
    translation = float(np.linalg.norm(tgt_pos - cur_pos))
    r_cur = Rotation.from_quat([cur_q[1], cur_q[2], cur_q[3], cur_q[0]])
    r_tgt = Rotation.from_quat([tgt_q[1], tgt_q[2], tgt_q[3], tgt_q[0]])
    rotation = float(np.linalg.norm((r_tgt * r_cur.inv()).as_rotvec()))
    return translation, rotation


def validate_bounded(current, target):
    """Enforce the per-decision bounded-action limit shared with simulation."""
    t, r = bounded_decision_error(current, target)
    if t > MAX_DECISION_TRANSLATION_M or r > MAX_DECISION_ROTATION_RAD:
        raise ValueError(
            f"Bounded decision exceeded: translation={t:.4f}m (limit {MAX_DECISION_TRANSLATION_M}m), "
            f"rotation={r:.4f}rad (limit {MAX_DECISION_ROTATION_RAD}rad)"
        )
    return t, r


def validate_workspace(position, limits):
    """Reject targets outside an axis-aligned workspace box.

    `limits` is per-axis [min, max]: [[xmin, xmax], [ymin, ymax], [zmin, zmax]].
    """
    p = np.asarray(position, float)
    box = np.asarray(limits, float)
    if (
        p.shape != (3,)
        or box.shape != (3, 2)
        or not np.isfinite(p).all()
        or not np.isfinite(box).all()
        or np.any(box[:, 0] > box[:, 1])
    ):
        raise ValueError("Expected 3-D position and per-axis [[min,max]] workspace")
    lo, hi = box[:, 0], box[:, 1]
    if np.any(p < lo) or np.any(p > hi):
        raise ValueError(
            f"EEF target outside workspace: {p.tolist()} not in {box.tolist()}"
        )


def validate_joint_limits(joints, limits):
    """Reject a 6-joint command outside the per-joint [min, max] limits.

    `limits` is [[min1,max1], ..., [min6,max6]].
    """
    j = np.asarray(joints, float)
    box = np.asarray(limits, float)
    if (
        j.shape != (6,)
        or box.shape != (6, 2)
        or not np.isfinite(j).all()
        or not np.isfinite(box).all()
        or np.any(box[:, 0] > box[:, 1])
    ):
        raise ValueError("Expected 6 joints and per-joint [[min,max]] limits")
    lo, hi = box[:, 0], box[:, 1]
    if np.any(j < lo) or np.any(j > hi):
        raise ValueError(f"Joint command outside limits: {j.tolist()}")


def assemble_observation(
    cameras, states, eef_positions, eef_quaternions_wxyz, instruction, remaining_steps
):
    """Build the teacher_observation dict in the simulator wire format."""
    states = np.asarray(states, np.float32)
    if states.shape != (ACTION_DIM,):
        raise ValueError(f"Expected 14-dim state, got {states.shape}")
    eef_positions = np.asarray(eef_positions, np.float32).reshape(2, 3)
    eef_quaternions_wxyz = np.asarray(eef_quaternions_wxyz, np.float32).reshape(2, 4)
    return dict(
        **cameras,
        states=states,
        eef_positions=eef_positions,
        eef_quaternions_wxyz=eef_quaternions_wxyz,
        instruction=instruction,
        remaining_steps=int(remaining_steps),
    )
