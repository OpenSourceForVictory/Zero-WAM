import importlib.util
import io
from pathlib import Path
import sys
import time
from types import SimpleNamespace as NS

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1] / 'evaluation/piper175/inference_client'


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


camera = load('piper175_test_camera', ROOT / 'camera_manager.py')


def payload(stamps=None, serials=None):
    buffer = io.BytesIO()
    np.savez(buffer, high=np.full((480,640,3),1,dtype=np.uint8),
             left=np.full((480,640,3),2,dtype=np.uint8),
             right=np.full((480,640,3),3,dtype=np.uint8),
             timestamps=[99.95,99.96,99.97] if stamps is None else stamps,
             serials=camera.SERIALS if serials is None else serials)
    return buffer.getvalue()


def test_lossless_role_order():
    frames, _ = camera.decode_observation(payload(), now=100)
    assert [int(f[0,0,0]) for f in frames] == [1,2,3]


@pytest.mark.parametrize('kwargs', [dict(stamps=[98,98,98]), dict(stamps=[101,101,101]),
    dict(stamps=[99.7,99.95,99.95]), dict(stamps=[float('nan'),99.95,99.95]),
    dict(serials=list(reversed(camera.SERIALS)))])
def test_camera_rejects_bad_observation(kwargs):
    with pytest.raises(RuntimeError):
        camera.decode_observation(payload(**kwargs), now=100)


@pytest.fixture
def controller(monkeypatch):
    # Exercise the production guard without importing or connecting hardware.
    monkeypatch.setitem(sys.modules, 'pyAgxArm', NS(create_agx_arm_config=None,
        AgxArmFactory=None, ArmModel=None, PiperFW=None))
    original = load('piper175_test_original', ROOT / 'piper_controller.py')
    monkeypatch.setitem(sys.modules, 'piper_controller', original)
    monkeypatch.setattr(sys, 'path', sys.path.copy())
    monkeypatch.setitem(sys.modules, 'stop_request', load('piper175_test_stop', ROOT / 'stop_request.py'))
    module = load('piper175_test_controller', ROOT / 'site_controller.py')
    c = module.PiperController.__new__(module.PiperController)
    c.stop_check = lambda: False
    c.max_target_delta_deg = 15
    c.gripper_mode = 'hold'
    c.gripper_max = [.1,.07]
    c.actuated = False
    c.calls = []
    def arm(label):
        status = NS(ctrl_mode=0, teach_status=0, arm_status=0, mode_feedback=1, motion_status=1,
            err_status=NS(**{f'{prefix}{i}{suffix}':False for i in range(1,7)
                for prefix,suffix in [('joint_','_angle_limit'),('communication_status_joint_','')]}))
        return NS(status=status,
            get_joint_angles=lambda: NS(timestamp=time.time(),msg=[0.0]*6),
            get_arm_status=lambda: NS(timestamp=time.time(),msg=status),
            get_joints_enable_status_list=lambda: [True]*6,
            enable=lambda: c.calls.append((label,'enable')) or True,
            move_j=lambda v: c.calls.append((label,'move',v)))
    c.robot_l, c.robot_r = arm('left'), arm('right')
    c.gripper_l = NS(move_gripper_m=lambda v,force: c.calls.append(('left','grip',v)))
    c.gripper_r = NS(move_gripper_m=lambda v,force: c.calls.append(('right','grip',v)))
    return c


def test_teaching_on_right_prevents_either_enable(controller):
    controller.robot_r.status.ctrl_mode = 2
    with pytest.raises(RuntimeError):
        controller.enable()
    assert not controller.calls


@pytest.mark.parametrize('bad', [[float('nan')]*6, [1.0]*6])
def test_invalid_or_distant_target_sends_nothing(controller,bad):
    with pytest.raises((RuntimeError,ValueError)):
        controller.send_joints_with_gripper(bad,[0]*6,0,0)
    assert not controller.calls


def test_hold_and_175_gripper_caps(controller):
    controller.send_joints_with_gripper([0]*6,[0]*6,.1,.1)
    assert [c[1] for c in controller.calls] == ['move','move']
    controller.calls.clear()
    controller.gripper_mode='legacy174'
    controller.send_joints_with_gripper([0]*6,[0]*6,.1,.1)
    assert controller.calls[-2:] == [('left','grip',.08),('right','grip',.07)]


def test_stop_prevents_send(controller):
    controller.stop_check=lambda: True
    with pytest.raises(RuntimeError):
        controller.send_joints_with_gripper([0]*6,[0]*6,0,0)
    assert not controller.calls


def test_174_execution_accepts_large_finite_target_without_optional_guard(controller):
    controller.max_target_delta_deg = None
    controller.send_joints_with_gripper([1.0]*6, [0]*6, 0, 0)
    assert [c[1] for c in controller.calls] == ['move', 'move']
    controller.calls.clear()
    with pytest.raises(ValueError):
        controller.send_joints_with_gripper([float('nan')]*6, [0]*6, 0, 0)
    assert not controller.calls
    controller.robot_r.status.ctrl_mode = 2
    with pytest.raises(RuntimeError):
        controller.send_joints_with_gripper([1.0]*6, [0]*6, 0, 0)
    assert not controller.calls


def test_174_wait_returns_false_instead_of_raising(controller):
    assert controller.wait_both_motion_done([.1]*6, [.1]*6,
        timeout=.001, poll_interval=.001) is False
    assert not controller.calls


def test_174_wait_accepts_status_or_angles(controller):
    assert controller.wait_both_motion_done([0]*6, [0]*6) is True
    controller.robot_l.status.motion_status = 0
    controller.robot_r.status.motion_status = 0
    assert controller.wait_both_motion_done([.1]*6, [.1]*6) is True
    assert not controller.calls


@pytest.mark.parametrize('kind, expect_stop', [('user', False), ('signal', False), ('fault', True)])
def test_exit_handlers_separate_operator_stop_from_fault(kind, expect_stop):
    # Exercise the actual production exception handlers without hardware/import side effects.
    import ast
    source = ast.parse((ROOT / 'real_robot_inference_merged.py').read_text())
    run = next(n for n in source.body if isinstance(n, ast.FunctionDef) and n.name == 'run_inference')
    block = next(n for n in run.body if isinstance(n, ast.Try))
    handlers = block.handlers
    stop_type = load('piper175_exit_stop', ROOT / 'stop_request.py').StopRequested
    exc = {'user': stop_type('stop'), 'signal': KeyboardInterrupt(), 'fault': RuntimeError('fault')}[kind]
    calls = []
    scope = {'StopRequested': stop_type, 'injected': exc, 'stopped': False, 'error': None,
             'piper': NS(actuated=True, emergency_stop=lambda: calls.append('estop')),
             'latch_stop': lambda: calls.append('software_stop')}
    tree = ast.Module(body=[ast.Try(body=[ast.Raise(exc=ast.Name(id='injected', ctx=ast.Load()))],
                     handlers=handlers, orelse=[], finalbody=[])], type_ignores=[])
    code = compile(ast.fix_missing_locations(tree), '<production-exit-handlers>', 'exec')
    if expect_stop:
        with pytest.raises(RuntimeError, match='fault'):
            exec(code, scope)
    else:
        exec(code, scope)
        assert scope['stopped'] is True
        assert scope['error'] is None
    assert calls == ['software_stop']
