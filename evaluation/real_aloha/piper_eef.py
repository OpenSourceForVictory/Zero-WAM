"""Prepare Piper MOVE_P arguments without importing an SDK or commanding arms.

The caller supplies the same frame/TCP definitions used by its existing EEF
controller. Identity base transforms explicitly select independent arm bases.
This module does not certify reachability, collisions, or installation frames.
"""

import numpy as np
from scipy.spatial.transform import Rotation

from .action_adapter import ActionAdapter, limit_targets
from .robot_bridge.geometry import transform


def _transforms(value, name):
    value = np.asarray(value, float)
    if value.shape != (2, 4, 4) or not np.isfinite(value).all():
        raise ValueError(name + " requires two finite 4x4 transforms")
    for matrix in value:
        r = matrix[:3, :3]
        if (not np.allclose(matrix[3], [0, 0, 0, 1])
                or not np.allclose(r.T @ r, np.eye(3), atol=1e-6)
                or not np.isclose(np.linalg.det(r), 1, atol=1e-6)):
            raise ValueError(name + " must contain rigid transforms")
    return value.copy()


class PiperEEFAdapter:
    """Relative Zero-WAM EEF -> absolute per-arm Piper flange xyz/rpy.

    task_from_base[i] maps arm i's base into the action reconstruction frame.
    flange_from_tcp[i] describes the controlled TCP in that arm's flange frame.
    For legacy --no_transform and --tcp_offset=0 both are explicitly identities.
    gripper_max_m contains measured full openings, one for each arm.
    """

    def __init__(self, initial_flange_poses, *, task_from_base,
                 flange_from_tcp, policy_to_robot_rotation, gripper_max_m):
        self.task_from_base = _transforms(task_from_base, "task_from_base")
        self.flange_from_tcp = _transforms(flange_from_tcp, "flange_from_tcp")
        self.gripper_max_m = np.asarray(gripper_max_m, float)
        if (self.gripper_max_m.shape != (2,)
                or not np.isfinite(self.gripper_max_m).all()
                or np.any(self.gripper_max_m <= 0)):
            raise ValueError("Two positive measured gripper openings required")
        initial = self._observation(initial_flange_poses)
        self.adapter = ActionAdapter(initial["eef_positions"],
                                     initial["eef_quaternions_wxyz"],
                                     policy_to_robot_rotation)

    def _observation(self, flange_poses):
        poses = np.asarray(flange_poses, float)
        if poses.shape != (2, 6) or not np.isfinite(poses).all():
            raise ValueError("Expected two finite flange xyz/rpy poses in m/rad")
        positions, quaternions = [], []
        for i, pose in enumerate(poses):
            matrix = np.eye(4)
            matrix[:3, :3] = Rotation.from_euler("xyz", pose[3:]).as_matrix()
            matrix[:3, 3] = pose[:3]
            tcp = self.task_from_base[i] @ matrix @ self.flange_from_tcp[i]
            positions.append(tcp[:3, 3])
            quaternions.append(Rotation.from_matrix(tcp[:3, :3]).as_quat()[[3, 0, 1, 2]])
        return dict(eef_positions=np.asarray(positions),
                    eef_quaternions_wxyz=np.asarray(quaternions))

    def prepare(self, action, current_flange_poses, current_grippers_m, *,
                follow_grippers=False, max_translation=0.004, max_rotation=0.025):
        """Return a preview; requires fresh measured poses/openings from caller.

        Output matches send_end_pose_with_gripper(left, right, left_g, right_g).
        Openings are held by default. No SDK methods are called here.
        """
        openings = np.asarray(current_grippers_m, float)
        if (openings.shape != (2,) or not np.isfinite(openings).all()
                or np.any(openings < 0) or np.any(openings > self.gripper_max_m)):
            raise ValueError("Measured gripper opening outside calibrated range")
        current = self._observation(current_flange_poses)
        self.adapter.hold_grippers = not follow_grippers
        targets = self.adapter.absolute_targets(action, openings / self.gripper_max_m)
        targets, diagnostics = limit_targets(current, targets, max_translation, max_rotation)
        poses, grippers = [], []
        for i, arm in enumerate(("left", "right")):
            target = targets[arm]
            tcp = transform(target["position"], target["quaternion_wxyz"])
            flange = np.linalg.inv(self.task_from_base[i]) @ tcp @ np.linalg.inv(self.flange_from_tcp[i])
            poses.append(np.r_[flange[:3, 3], Rotation.from_matrix(flange[:3, :3]).as_euler("xyz")].tolist())
            grippers.append(float(target["gripper_opening"] * self.gripper_max_m[i]))
        return dict(controller_method="send_end_pose_with_gripper",
                    controller_arguments=[poses[0], poses[1], *grippers],
                    targets=targets, diagnostics=diagnostics,
                    physical_commands=0, executable=False,
                    note="Format preview only; no reachability/collision or feedback freshness verification")
