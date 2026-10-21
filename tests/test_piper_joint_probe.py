import numpy as np
import pytest
from evaluation.real_aloha.run_piper_joint_probe import joint_targets, check_targets, plan_approach


def test_channels_and_padding():
    action = np.zeros((28, 2, 16))
    action[:, 0] = 999
    action[:14, 1] = 888
    action[26:, 1] = 777
    action[14:26, 1] = np.arange(12)[:, None]
    target = joint_targets(action)
    assert target.shape == (16, 2, 6)
    np.testing.assert_equal(target[0], np.arange(12).reshape(2, 6))
    action[14, 1, 0] = np.nan
    with pytest.raises(ValueError):
        joint_targets(action)


def test_boundary_start_allowed_only_with_interior_small_target():
    limits = np.tile([0., 3.], (2, 6, 1))
    current = np.zeros((2, 6))
    target = np.full((1, 2, 6), np.deg2rad(2))
    assert check_targets(target, current, limits)['first_max_joint_change_deg'] == 2
    with pytest.raises(ValueError, match='limits/margin'):
        check_targets(-target, current, limits)
    assert check_targets(target * 4, current, limits)['first_max_joint_change_deg'] == 8
    with pytest.raises(ValueError, match='excursion'):
        check_targets(target * 10, current, limits)


def test_discontinuous_sequence_rejected():
    limits = np.tile([-3., 3.], (2, 6, 1))
    targets = np.zeros((2, 2, 6))
    targets[1, 0, 0] = np.deg2rad(4)
    with pytest.raises(ValueError, match='Adjacent'):
        check_targets(targets, targets[0], limits)


def test_supervised_approach_segments_and_boundaries():
    current=np.zeros((2,6));limits=np.tile([0.,3.],(2,6,1))
    target=np.deg2rad(np.full((2,6),27.1))
    path=plan_approach(current,target,limits)
    assert len(path)==28
    assert np.rad2deg(abs(np.diff(np.concatenate([current[None],path]),axis=0))).max()<=1
    np.testing.assert_allclose(path[-1],target)
    with pytest.raises(ValueError,match='30 deg'):
        plan_approach(current,target*2,limits)
    with pytest.raises(ValueError,match='limits'):
        plan_approach(current,-target,limits)
