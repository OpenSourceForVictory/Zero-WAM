"""Offline tests: no robot SDK, network connection, or hardware is used."""
import ast
from datetime import datetime
import json
from pathlib import Path
import signal
import sys
from types import SimpleNamespace as NS

import numpy as np
import pytest

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))
from fault_stop import latch_software_stop
from stop_request import StopRequested


@pytest.mark.parametrize('initial', ['{"emergency_stop": false, "keep": 12}', '{bad', '[]'])
def test_atomic_latch(initial, tmp_path):
    p = tmp_path/'runtime_config.json'; p.write_text(initial)
    latch_software_stop(p)
    data = json.loads(p.read_text())
    assert data['emergency_stop'] is True
    if 'keep' in initial:
        assert data['keep'] == 12
    assert not list(tmp_path.glob('*.tmp'))


@pytest.mark.parametrize('fault', [RuntimeError('Missing or stale arm feedback'),
    TimeoutError('Inference timeout'), ValueError('Invalid joint action tensor'),
    OSError('Camera lost'), SystemExit('unexpected exit'),
    StopRequested('operator stop'), KeyboardInterrupt()])
@pytest.mark.parametrize('cleanup_failure', [None, 'video', 'result', 'latch'])
def test_production_exit_sends_no_controller_commands(fault, cleanup_failure, tmp_path, monkeypatch):
    tree = ast.parse((ROOT/'real_robot_inference_merged.py').read_text())
    run = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'run_inference')
    calls = []
    config_path = tmp_path/'runtime_config.json'
    config_path.write_text('{"emergency_stop": false}')
    def fail(*a, **kw):
        raise OSError('injected write failure')
    if cleanup_failure == 'latch':
        monkeypatch.setattr('fault_stop.latch_software_stop', fail)
    if cleanup_failure == 'result':
        original_write = Path.write_text
        def write(path, *a, **kw):
            if path.name == 'result.json':
                return fail()
            return original_write(path, *a, **kw)
        monkeypatch.setattr(Path, 'write_text', write)
    controller = NS(actuated=False, can_left='left', can_right='right', gripper_max=[.08,.07],
        connect=lambda: None, get_joint_angles=lambda: np.zeros(12),
        close=lambda: calls.append('disconnect'))
    for name in ['emergency_stop', 'enable', 'disable', 'reset', 'send_joints_with_gripper']:
        setattr(controller, name, lambda *a, _n=name, **kw: calls.append(_n))
    camera = NS(start=lambda: None, get_frames=lambda: [np.zeros((2,2,3))]*3,
                timestamps=np.zeros(3), stop=lambda: calls.append('camera_close'))
    client = NS(connect=lambda: {}, close=lambda: calls.append('client_close'))
    class Logger:
        def __init__(self, path, prompt):
            self.path = Path(path); self.video_writers = None
            if cleanup_failure == 'video':
                self.video_writers = {'fake': NS(release=fail)}
        def start(self, *args):
            self.path.mkdir(parents=True); self.save_dir = self.path
    def loop(piper, camera, client, cfg, logger, smoother):
        piper.actuated = True; logger.sent_steps = 102
        raise fault
    monkeypatch.setattr(signal, 'signal', lambda *a: None)
    cfg = NS(save_dir=str(tmp_path/'logs'), prompt='breakfast', server_host='unused', server_port=1,
             max_target_delta_deg=None, gripper_mode='hold', skip_server=False, run_mode='execute',
             movej_speed_percent=.06, smooth_method='none', smooth_factor=.1, v_max=1, action_dt=.02,
             outlier_threshold=10,savgol_window=3,savgol_order=2,butter_cutoff=1)
    scope = dict(Path=Path, datetime=datetime,json=json,np=np,os=NS(environ={}),signal=signal,
        StopRequested=StopRequested, CameraManager=lambda:camera, PiperController=lambda:controller,
        WebsocketClientPolicy=lambda **kw:client, InferenceLogger=Logger,
        JointTrajectorySmoother=lambda **kw:None, _check_emergency_stop=lambda:False,
        _inference_loop=loop, __file__=str(tmp_path/'real_robot_inference_merged.py'))
    exec(compile(ast.Module(body=[run],type_ignores=[]),'<production run_inference>','exec'),scope)
    if isinstance(fault, (StopRequested, KeyboardInterrupt)):
        scope['run_inference'](cfg)
    else:
        with pytest.raises(type(fault)):
            scope['run_inference'](cfg)
    assert calls == ['client_close','camera_close','disconnect']
    assert json.loads(config_path.read_text())['emergency_stop'] is (cleanup_failure != 'latch')
    if cleanup_failure == 'result':
        return
    result=json.loads(next((tmp_path/'logs').glob('*/result.json')).read_text())
    assert result['sent_steps']==102 and not result['completed']
    assert result['software_stop_latched'] is (cleanup_failure != 'latch')
    assert not result['automatic_electronic_stop_sent']


def test_latch_failure_does_not_call_hardware(tmp_path):
    with pytest.raises(OSError):
        latch_software_stop(tmp_path/'missing'/'runtime_config.json')


def test_rpc_stop_prevents_any_send(monkeypatch):
    import va_client
    c=va_client.WebsocketClientPolicy()
    calls=[]
    c._ws=NS(send=lambda *a:calls.append('send'))
    monkeypatch.setattr(c,'_stopped',lambda:True)
    with pytest.raises(StopRequested):
        c.infer({'obs':{}})
    assert calls==[]
