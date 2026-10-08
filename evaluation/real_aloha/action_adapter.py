"""Convert released RoboTwin actions into calibrated dual-Piper EEF targets."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.spatial.transform import Rotation, Slerp


ARMS = ("left", "right")


def _unit_xyzw(quaternion):
    quaternion = np.asarray(quaternion, dtype=np.float64)
    if quaternion.shape != (4,) or not np.isfinite(quaternion).all():
        raise ValueError("Expected a finite xyzw quaternion")
    norm = np.linalg.norm(quaternion)
    if norm < 1e-8:
        raise ValueError("Zero-length quaternion")
    return quaternion / norm


def _wxyz_to_xyzw(quaternion):
    quaternion = np.asarray(quaternion, dtype=np.float64)
    if quaternion.shape != (4,):
        raise ValueError("Expected a wxyz quaternion")
    return _unit_xyzw(quaternion[[1, 2, 3, 0]])


def _xyzw_to_wxyz(quaternion):
    quaternion = _unit_xyzw(quaternion)
    return quaternion[[3, 0, 1, 2]]


def _validate_axis_map(axis_map):
    axis_map = np.asarray(axis_map, dtype=np.float64)
    if (
        axis_map.shape != (3, 3)
        or not np.isfinite(axis_map).all()
        or not np.allclose(axis_map.T @ axis_map, np.eye(3), atol=1e-6)
        or not np.isclose(np.linalg.det(axis_map), 1.0, atol=1e-6)
    ):
        raise ValueError("policy_to_robot_rotation must be a proper 3x3 rotation")
    return axis_map


@dataclass
class ActionAdapter:
    """Reconstruct absolute EEF targets using the released evaluation convention.

    Translation is relative to the episode-start EEF position. Quaternion deltas
    are post-multiplied onto the episode-start orientation, matching RoboTwin.
    The translation axis map is an installation calibration and must not be
    estimated from a single pose.
    """

    initial_positions: np.ndarray
    initial_quaternions_wxyz: np.ndarray
    policy_to_robot_rotation: np.ndarray
    hold_grippers: bool = True
    gripper_threshold: float = 0.5

    def __post_init__(self):
        self.initial_positions = np.asarray(self.initial_positions, np.float64)
        self.initial_quaternions_wxyz = np.asarray(
            self.initial_quaternions_wxyz, np.float64
        )
        self.policy_to_robot_rotation = _validate_axis_map(
            self.policy_to_robot_rotation
        )
        if self.initial_positions.shape != (2, 3):
            raise ValueError("Expected two initial EEF positions")
        if self.initial_quaternions_wxyz.shape != (2, 4):
            raise ValueError("Expected two initial EEF quaternions")
        for quaternion in self.initial_quaternions_wxyz:
            _wxyz_to_xyzw(quaternion)
        if not 0.0 <= self.gripper_threshold <= 1.0:
            raise ValueError("gripper_threshold must be in [0, 1]")

    def absolute_targets(self, action, current_openings):
        action = np.asarray(action, dtype=np.float64).reshape(-1)
        current_openings = np.asarray(current_openings, dtype=np.float64)
        if action.shape != (16,) or not np.isfinite(action).all():
            raise ValueError("Expected one finite 16D Zero-WAM action")
        if current_openings.shape != (2,) or np.any(
            (current_openings < 0) | (current_openings > 1)
        ):
            raise ValueError("Expected two normalized gripper openings")

        result = {}
        for index, arm in enumerate(ARMS):
            offset = index * 8
            delta_position = self.policy_to_robot_rotation @ action[offset : offset + 3]
            delta_rotation = Rotation.from_quat(
                _unit_xyzw(action[offset + 3 : offset + 7])
            )
            initial_rotation = Rotation.from_quat(
                _wxyz_to_xyzw(self.initial_quaternions_wxyz[index])
            )
            target_rotation = initial_rotation * delta_rotation
            opening = (
                float(current_openings[index])
                if self.hold_grippers
                else float(np.clip(action[offset + 7], 0.0, 1.0))
            )
            result[arm] = dict(
                position=(self.initial_positions[index] + delta_position).tolist(),
                quaternion_wxyz=_xyzw_to_wxyz(target_rotation.as_quat()).tolist(),
                gripper_opening=opening,
                gripper_closed=bool(opening < self.gripper_threshold),
            )
        return result


def limit_targets(current_observation, targets, max_translation, max_rotation):
    """Limit each target from current feedback without changing its endpoint."""
    positions = np.asarray(current_observation["eef_positions"], dtype=np.float64)
    quaternions = np.asarray(
        current_observation["eef_quaternions_wxyz"], dtype=np.float64
    )
    if positions.shape != (2, 3) or quaternions.shape != (2, 4):
        raise ValueError("Invalid EEF feedback")
    if max_translation <= 0 or max_rotation <= 0:
        raise ValueError("Motion limits must be positive")

    limited = {}
    diagnostics = {}
    for index, arm in enumerate(ARMS):
        current_position = positions[index]
        target_position = np.asarray(targets[arm]["position"], dtype=np.float64)
        delta = target_position - current_position
        distance = float(np.linalg.norm(delta))
        position_scale = min(1.0, max_translation / max(distance, 1e-12))
        output_position = current_position + position_scale * delta

        current_rotation = Rotation.from_quat(_wxyz_to_xyzw(quaternions[index]))
        target_rotation = Rotation.from_quat(
            _wxyz_to_xyzw(targets[arm]["quaternion_wxyz"])
        )
        angle = float((target_rotation * current_rotation.inv()).magnitude())
        rotation_scale = min(1.0, max_rotation / max(angle, 1e-12))
        if rotation_scale < 1.0:
            rotations = Rotation.from_quat(
                np.stack([current_rotation.as_quat(), target_rotation.as_quat()])
            )
            output_rotation = Slerp([0.0, 1.0], rotations)([rotation_scale])[0]
        else:
            output_rotation = target_rotation

        limited[arm] = dict(
            position=output_position.tolist(),
            quaternion_wxyz=_xyzw_to_wxyz(output_rotation.as_quat()).tolist(),
            gripper_opening=float(targets[arm]["gripper_opening"]),
            gripper_closed=bool(targets[arm]["gripper_closed"]),
        )
        diagnostics[arm] = dict(
            requested_translation_m=distance,
            requested_rotation_rad=angle,
            translation_scale=position_scale,
            rotation_scale=rotation_scale,
        )
    return limited, diagnostics
