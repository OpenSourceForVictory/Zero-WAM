import math
import time
from types import SimpleNamespace as NS

import pytest

from evaluation.piper175.arm import check_motion


def arm(ctrl=0, teach=0, age=0, fault=0, joint_fault=False):
    errors = {}
    for i in range(1, 7):
        errors[f'joint_{i}_angle_limit'] = False
        errors[f'communication_status_joint_{i}'] = joint_fault
    stamp = time.time() - age
    return NS(
        get_joint_angles=lambda: NS(timestamp=stamp, msg=[0.0] * 6),
        get_arm_status=lambda: NS(timestamp=stamp, msg=NS(
            ctrl_mode=ctrl, teach_status=teach, arm_status=fault, err_status=NS(**errors))))


@pytest.mark.parametrize('kwargs', [dict(ctrl=2), dict(teach=1), dict(teach=2),
                                   dict(age=2), dict(age=-2), dict(fault=1),
                                   dict(joint_fault=True)])
def test_rejects_unfit_motion_feedback(kwargs):
    with pytest.raises(RuntimeError):
        check_motion(arm(**kwargs))


def test_normal_feedback_and_nonfinite():
    assert check_motion(arm()) == [0.0] * 6
    a = arm()
    a.get_joint_angles = lambda: NS(timestamp=time.time(), msg=[math.nan] * 6)
    with pytest.raises(RuntimeError):
        check_motion(a)
