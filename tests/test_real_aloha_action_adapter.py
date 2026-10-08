import json
from argparse import Namespace

import numpy as np
import pytest
from scipy.spatial.transform import Rotation

from evaluation.real_aloha.action_adapter import ActionAdapter, limit_targets
from evaluation.real_aloha.run_policy import (
    _formatted_observation,
    _load_calibration,
    run_episode,
)


def test_action_adapter_matches_robotwin_initial_pose_composition():
    initial_positions = np.array([[1.0, 2.0, 3.0], [-1.0, 0.5, 0.2]])
    initial_xyzw = Rotation.from_euler("z", [90, -30], degrees=True).as_quat()
    initial_wxyz = initial_xyzw[:, [3, 0, 1, 2]]
    adapter = ActionAdapter(initial_positions, initial_wxyz, np.diag([-1.0, -1.0, 1.0]))
    action = np.zeros(16)
    action[0:3] = [0.1, 0.2, 0.3]
    action[3:7] = Rotation.from_euler("x", 0.2).as_quat()
    action[7] = 0.0
    action[8:11] = [-0.1, 0.1, 0.0]
    action[11:15] = Rotation.from_euler("y", -0.1).as_quat()
    action[15] = 0.0

    targets = adapter.absolute_targets(action, [0.8, 0.4])

    assert np.allclose(targets["left"]["position"], [0.9, 1.8, 3.3])
    assert targets["left"]["gripper_opening"] == pytest.approx(0.8)
    output_left = Rotation.from_quat(
        np.asarray(targets["left"]["quaternion_wxyz"])[[1, 2, 3, 0]]
    )
    expected_left = Rotation.from_quat(initial_xyzw[0]) * Rotation.from_euler("x", 0.2)
    assert (output_left * expected_left.inv()).magnitude() < 1e-8


def test_target_limiter_caps_translation_and_rotation():
    observation = dict(
        eef_positions=np.zeros((2, 3)),
        eef_quaternions_wxyz=np.array([[1, 0, 0, 0], [1, 0, 0, 0]]),
    )
    quaternion = Rotation.from_euler("z", 1.0).as_quat()[[3, 0, 1, 2]]
    targets = {
        arm: dict(
            position=[0.1, 0, 0],
            quaternion_wxyz=quaternion.tolist(),
            gripper_opening=0.5,
            gripper_closed=False,
        )
        for arm in ("left", "right")
    }
    limited, diagnostics = limit_targets(observation, targets, 0.004, 0.025)

    assert np.linalg.norm(limited["left"]["position"]) == pytest.approx(0.004)
    output = Rotation.from_quat(
        np.asarray(limited["left"]["quaternion_wxyz"])[[1, 2, 3, 0]]
    )
    assert output.magnitude() == pytest.approx(0.025)
    assert diagnostics["left"]["translation_scale"] == pytest.approx(0.04)


def test_calibration_must_be_explicitly_verified(tmp_path):
    path = tmp_path / "calibration.json"
    path.write_text(
        json.dumps({"verified": False, "policy_to_robot_rotation": np.eye(3).tolist()})
    )
    with pytest.raises(ValueError, match="unverified"):
        _load_calibration(path)


def test_observation_keys_match_released_policy_contract():
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    observation = dict(
        cam_high=frame,
        cam_left_wrist=frame,
        cam_right_wrist=frame,
        states=np.zeros(14, dtype=np.float32),
    )
    formatted = _formatted_observation(observation, "task")
    assert set(formatted) == {
        "observation.images.cam_high",
        "observation.images.cam_left_wrist",
        "observation.images.cam_right_wrist",
        "observation.state",
        "task",
    }
    assert formatted["observation.state"].shape == (14,)


class _FakeRobot:
    def __init__(self):
        self.identity = None
        self.preview_calls = 0
        self.execute_calls = 0
        self.finished = None
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        self.obs = dict(
            cam_high=frame,
            cam_left_wrist=frame,
            cam_right_wrist=frame,
            states=np.array([0] * 6 + [0.8] + [0] * 6 + [0.7], np.float32),
            eef_positions=np.array([[0.2, 0.1, 0.3], [0.2, -0.1, 0.3]]),
            eef_quaternions_wxyz=np.array([[1, 0, 0, 0], [1, 0, 0, 0]]),
        )

    def reset(self, **_):
        self.identity = dict(episode_id="fake", step_id=0)
        return dict(
            episode_id="fake",
            step_id=0,
            metadata=dict(motion_enabled=False),
        )

    def observation(self):
        return self.obs

    def preview_eef(self, _):
        self.preview_calls += 1
        return dict(action=[0.0] * 14)

    def execute(self, proposal):
        self.execute_calls += 1
        return proposal

    def finish(self, reason):
        self.finished = reason


class _FakePolicy:
    def __init__(self):
        self.calls = []

    def infer(self, request):
        self.calls.append(request)
        if request.get("reset"):
            return {}
        if request.get("compute_kv_cache"):
            return {}
        action = np.zeros((16, 2, 4), dtype=np.float32)
        action[3, :, :] = 0.0
        action[4, :, :] = 0.0
        action[5, :, :] = 0.0
        action[6, :, :] = 1.0
        action[11, :, :] = 0.0
        action[12, :, :] = 0.0
        action[13, :, :] = 0.0
        action[14, :, :] = 1.0
        return dict(action=action)


def test_shadow_episode_never_previews_or_executes(tmp_path):
    calibration = tmp_path / "calibration.json"
    calibration.write_text(
        json.dumps({"verified": True, "policy_to_robot_rotation": np.eye(3).tolist()})
    )
    args = Namespace(
        output=tmp_path / "result",
        calibration=calibration,
        robot_host="unused",
        robot_port=0,
        robot_timeout=1,
        policy_host="unused",
        policy_port=0,
        policy_connect_timeout=1,
        policy_timeout=1,
        mode="shadow",
        seed=0,
        follow_grippers=False,
        prompt="put the object on the scale",
        no_icl=False,
        icl_id="place_object_scale",
        video_guidance_scale=-1,
        action_guidance_scale=1,
        icl_guidance_scale=5,
        max_decisions=1,
        max_actions=1,
        max_translation=0.004,
        max_rotation=0.025,
    )
    robot, policy = _FakeRobot(), _FakePolicy()

    summary = run_episode(args, robot=robot, policy=policy)

    assert summary["actions"] == 1
    assert robot.preview_calls == 0
    assert robot.execute_calls == 0
    assert robot.finished == "zero_wam_completed"
    record = json.loads((args.output / "action_0000.json").read_text())
    assert record["mode"] == "shadow"
    assert record["limited_targets"]["left"]["gripper_opening"] == pytest.approx(0.8)
