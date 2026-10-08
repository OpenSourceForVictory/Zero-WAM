import numpy as np
import pytest
from scipy.spatial.transform import Rotation

from evaluation.real_aloha.piper_eef import PiperEEFAdapter


def setup_adapter():
    poses = np.array([[.2, .1, .3, .2, -.3, .4], [.3, -.1, .25, -.1, .2, -.4]])
    bases = np.repeat(np.eye(4)[None], 2, axis=0)
    bases[1, :3, :3] = Rotation.from_euler("z", .6).as_matrix()
    bases[1, :3, 3] = [0, -.5, 0]
    tools = np.repeat(np.eye(4)[None], 2, axis=0)
    tools[:, 2, 3] = [.12, .15]
    tools[1, :3, :3] = Rotation.from_euler("x", .3).as_matrix()
    adapter = PiperEEFAdapter(poses, task_from_base=bases, flange_from_tcp=tools,
                              policy_to_robot_rotation=np.eye(3), gripper_max_m=[.08, .07])
    action = np.zeros(16)
    action[[6, 14]] = 1
    return adapter, poses, action


def test_zero_delta_preserves_flange_with_rotated_bases_and_tools():
    adapter, poses, action = setup_adapter()
    result = adapter.prepare(action, poses, [.04, .035])
    np.testing.assert_allclose(result["controller_arguments"][:2], poses, atol=1e-12)
    np.testing.assert_allclose(result["controller_arguments"][2:], [.04, .035])
    assert result["executable"] is False
    assert result["physical_commands"] == 0


def test_translation_is_bounded_and_converted_to_arm_base():
    adapter, poses, action = setup_adapter()
    action[8] = .1
    result = adapter.prepare(action, poses, [.04, .035])
    expected = adapter.task_from_base[1, :3, :3].T @ [.004, 0, 0]
    np.testing.assert_allclose(np.array(result["controller_arguments"][1][:3])-poses[1, :3], expected, atol=1e-12)


def test_normalized_grippers_are_converted_to_meters_only_when_requested():
    adapter, poses, action = setup_adapter()
    action[[7, 15]] = [.25, .8]
    result = adapter.prepare(action, poses, [.04, .035], follow_grippers=True)
    np.testing.assert_allclose(result["controller_arguments"][2:], [.02, .056])


def test_stale_chunk_reference_is_not_rebased_to_current_pose():
    adapter, poses, action = setup_adapter()
    current = poses.copy()
    current[0, 0] += .02
    result = adapter.prepare(action, current, [.04, .035])
    assert result["controller_arguments"][0][0] == pytest.approx(current[0, 0]-.004)


@pytest.mark.parametrize("bad", [[-.01, .03], [.04, .09], [float('nan'), .03]])
def test_invalid_gripper_feedback_is_rejected(bad):
    adapter, poses, action = setup_adapter()
    with pytest.raises(ValueError):
        adapter.prepare(action, poses, bad)
