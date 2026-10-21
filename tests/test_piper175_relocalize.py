import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'evaluation/piper175/collection'))
from relocalize import pose_metrics, config_valid


def test_config_needs_two_complete_bases():
    one='"PositionSet":"1", "OOTXSet":"1"'
    assert not config_valid(one)
    assert config_valid(one+one)


def test_static_and_drifting_pose_metrics():
    rows=[[10+i*.01,10+i*.01-.002,0,0,0,0,0,0,1] for i in range(500)]
    assert pose_metrics(rows)['static_ok']
    for i,r in enumerate(rows):r[2]=i*.00002
    result=pose_metrics(rows)
    assert result['output_ok'] and not result['static_ok']
    assert result['range_mm'][0]>9
    assert not pose_metrics([])['output_ok']
    assert not pose_metrics(rows[:20])['output_ok']


def test_stale_socket_without_controller_allows_localization(monkeypatch):
    import relocalize as module
    import web_console
    import subprocess
    def refused(*args):raise ConnectionRefusedError()
    monkeypatch.setattr(web_console,'rpc',refused)
    monkeypatch.setattr(module.subprocess,'run',lambda *a,**k:subprocess.CompletedProcess(a,1))
    assert '已退出' in module.pause_controller()


def test_unreachable_live_controller_blocks_localization(monkeypatch):
    import pytest
    import relocalize as module
    import web_console
    import subprocess
    def refused(*args):raise ConnectionRefusedError()
    monkeypatch.setattr(web_console,'rpc',refused)
    monkeypatch.setattr(module.subprocess,'run',lambda *a,**k:subprocess.CompletedProcess(a,0))
    with pytest.raises(RuntimeError,match='仍存在'):module.pause_controller()


def test_faulted_controller_does_not_get_bypassed(monkeypatch):
    import pytest
    import relocalize as module
    import web_console
    monkeypatch.setattr(web_console,'rpc',lambda *a:{'phase':'fault','error':'fault'})
    with pytest.raises(RuntimeError,match='故障'):module.pause_controller()
