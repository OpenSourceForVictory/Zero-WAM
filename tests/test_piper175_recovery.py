import importlib.util
from pathlib import Path
from types import SimpleNamespace as NS
import sys
import pytest

ROOT = Path(__file__).resolve().parents[1] / 'evaluation/piper175'

@pytest.fixture
def module(monkeypatch):
    clock = NS(value=0.)
    monkeypatch.setitem(sys.modules, 'arm', NS(feedback=None, check_motion=lambda arm: [0]*6))
    spec = importlib.util.spec_from_file_location('recovery_test', ROOT/'recovery_feedback.py')
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    m.time = NS(monotonic=lambda: clock.value, time=lambda: clock.value,
                sleep=lambda dt: setattr(clock, 'value', clock.value+dt))
    return m, clock

def status(fault=0, teaching=0):
    return NS(msg=NS(ctrl_mode=0, teach_status=teaching, arm_status=fault,
        err_status=NS(**{f'{p}{i}{s}':False for i in range(1,7)
            for p,s in [('joint_','_angle_limit'),('communication_status_joint_','')]})))

def test_transient_communication_waits_until_stable(module):
    m, clock = module
    m.feedback = lambda arm: (None, status(5 if clock.value < .3 else 0))
    assert m.wait_after_reset(None) == [0]*6
    assert clock.value >= .8

def test_persistent_communication_times_out(module):
    m, _ = module
    m.feedback = lambda arm: (None, status(5))
    with pytest.raises(RuntimeError, match='did not stabilize'):
        m.wait_after_reset(None)

def test_teaching_does_not_get_tolerated(module):
    m, clock = module
    m.feedback = lambda arm: (None, status(teaching=1))
    with pytest.raises(RuntimeError, match='Teaching'):
        m.wait_after_reset(None)
    assert clock.value == 0
