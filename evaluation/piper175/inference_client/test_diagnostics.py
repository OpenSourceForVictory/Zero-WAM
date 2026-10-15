import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent))
from action_diagnostics import summarize_action


def test_aloha_channels_and_first_frame_skip():
    a = np.zeros((28, 2, 16))
    a[:, 0] = 999
    a[14:26, 1] = np.arange(16) * np.pi / 180
    a[26, 1] = .02
    a[27, 1] = .03
    a[6, 1] = a[13, 1] = 1
    r = summarize_action(a, True, np.zeros(12))
    assert r['usable_steps'] == 16
    assert r['joint_step_delta_max_deg'] == pytest.approx(1)
    assert r['joint_max_deg'] == pytest.approx([15]*12)
    assert r['gripper_max_m'] == [.02,.03]
    assert r['quaternion_norm_mae'] == [0,0]
    assert r['first_target_minus_current_deg'] == [0]*12


def test_invalid_output_is_reported_without_fake_metrics():
    a = np.zeros((28, 2, 16)); a[0, 0, 0] = np.nan
    assert summarize_action(a) == {'shape': [28,2,16], 'finite': False}


def test_14_channel_mapping():
    a = np.zeros((14,1,3)); a[6] = .04; a[13] = .05
    r = summarize_action(a)
    assert r['joint_max_deg'] == [0]*12
    assert r['gripper_max_m'] == [.04,.05]


def test_rpc_records_metadata_and_preserves_stop(monkeypatch, tmp_path):
    import va_client
    from stop_request import StopRequested
    c = va_client.WebsocketClientPolicy()
    reply = {'action': np.zeros((28,2,16)), 'diagnostics': {'wall_s': 1.2}}
    response = c._packer.pack(reply)
    c._ws = SimpleNamespace(send=lambda x: None, recv=lambda **kw: response)
    monkeypatch.setattr(c, '_stopped', lambda: False)
    c.diagnostics_path = tmp_path/'rpc.jsonl'
    result = c.infer({'obs': {}})
    assert np.array_equal(result['action'], reply['action'])
    assert c.last_timing['server']['wall_s'] == 1.2
    assert c.diagnostics_path.exists()
    monkeypatch.setattr(c, '_stopped', lambda: True)
    with pytest.raises(StopRequested):
        c.infer({'obs': {}})
