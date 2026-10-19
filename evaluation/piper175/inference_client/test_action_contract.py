import numpy as np
import pytest
from action_contract import CONTRACT, ORDER, execution_tensor, gripper_targets, validate_reset


def test_new_joint_and_gripper_mapping_preserves_server_input():
    raw = np.broadcast_to(np.arange(14)[:, None, None], (14, 2, 16)).copy()
    before = raw.copy()
    out = execution_tensor(raw, 'piperpika')
    np.testing.assert_array_equal(out[:, 0, 0], [0,1,2,3,4,5,12,6,7,8,9,10,11,13])
    np.testing.assert_array_equal(raw, before)
    assert not np.shares_memory(out, raw)


def test_legacy_mapping_and_gripper_behavior():
    raw = np.broadcast_to(np.arange(28)[:, None, None], (28, 2, 16))
    np.testing.assert_array_equal(execution_tensor(raw, 'legacy174')[:,0,0], [14,15,16,17,18,19,26,20,21,22,23,24,25,27])
    assert gripper_targets(.01, .04, 'legacy174') == pytest.approx((0, .036))
    assert gripper_targets(.01, .04, 'piperpika') == (.01, .04)


def test_wrong_contract_or_order_rejected():
    reply = dict(action_contract=CONTRACT, action_order=ORDER)
    validate_reset(reply, 'piperpika')
    validate_reset({}, 'legacy174')
    for invalid in ({}, None, dict(action_contract=CONTRACT, action_order=ORDER[::-1])):
        with pytest.raises(ValueError): validate_reset(invalid, 'piperpika')
    with pytest.raises(ValueError): validate_reset(reply, 'legacy174')


@pytest.mark.parametrize('a', [np.zeros((28,2,16)), np.zeros((14,2,3)), np.zeros((14,0,16)), np.full((14,2,16), np.nan)])
def test_invalid_new_prediction_rejected(a):
    with pytest.raises(ValueError): execution_tensor(a, 'piperpika')
