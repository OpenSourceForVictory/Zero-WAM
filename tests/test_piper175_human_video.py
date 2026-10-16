"""Synthetic camera only: exercises real MP4 encoding/decoding without robot IO."""
import json
from pathlib import Path
import sys
import threading
import time
import numpy as np
import pytest

ROOT=Path(__file__).resolve().parents[1]/'evaluation/piper175/collection'
sys.path.insert(0,str(ROOT))
import human_video as video
import web_console as web


def test_mp4_roundtrip_and_source_timestamps(tmp_path,monkeypatch):
    index=iter(range(30));start=time.time()
    def frame(*args):
        i=next(index)
        return start+i/30,np.full((48,64,3),i*5,dtype=np.uint8)
    monkeypatch.setattr(video,'read_frame',frame)
    out=tmp_path/'take'
    assert video.record(out,30,30,'unused',threading.Event())==0
    q=json.loads((out/'quality.json').read_text())
    assert q['frames']==q['decoded_frames']==30 and q['valid']
    assert (out/'video.mp4').exists() and not (out/'video.partial.mp4').exists()
    assert len((out/'timestamps.jsonl').read_text().splitlines())==30


def test_camera_failure_never_marks_video_complete(tmp_path,monkeypatch):
    def fail(*args):raise RuntimeError('camera disconnected')
    monkeypatch.setattr(video,'read_frame',fail)
    out=tmp_path/'take'
    assert video.record(out,30,30,'unused',threading.Event())==1
    assert not (out/'video.mp4').exists()
    assert 'camera disconnected' in json.loads((out/'quality.json').read_text())['failure_reasons']


def test_human_session_does_not_call_robot_and_uses_existing_shortcuts(tmp_path,monkeypatch):
    monkeypatch.setattr(web.Console,'poll',lambda _:None)
    monkeypatch.setattr(video,'read_frame',lambda *a:(time.time(),np.zeros((48,64,3),np.uint8)))
    monkeypatch.setattr(web,'ROOT',tmp_path)
    c=web.Console(tmp_path/'data')
    def forbidden(*a):raise AssertionError('Robot RPC must not be used')
    monkeypatch.setattr(c,'call',forbidden)
    c.do_session_start({'mode':'human_video','task':'demo','fps':30,'max_frames':50})
    assert c.collection_session['mode']=='human_video'
    started=[];monkeypatch.setattr(c,'start_video',lambda:started.append(True))
    c.do_start({});assert started==[True]
    (tmp_path/'.switch.sock').touch()
    c.do_exit({});assert c.collection_session is None
    c.closed.set()


def test_video_save_play_and_delete_are_isolated(tmp_path,monkeypatch):
    monkeypatch.setattr(web.Console,'poll',lambda _:None)
    c=web.Console(tmp_path/'data');task=c.root/'demo';raw=task/'human_video'
    episode=raw/'episode_20261016_120000_1';episode.mkdir(parents=True)
    (episode/'manifest.json').write_text('{"mode":"human_video","status":"complete"}')
    (episode/'quality.json').write_text('{"frames":30,"valid":true}')
    (episode/'video.mp4').write_bytes(b'test-video')
    class Done:
        def poll(self):return 0
        def wait(self,**kwargs):return 0
    c.collection_session={'mode':'human_video','task':'demo'}
    c.record={'mode':'human_video','phase':'recording','process':Done(),'raw':raw,'before':set()}
    c.finish(True)
    assert c.last_result['hdf5'] is None and c.last_result['saved']
    assert c.video_path('demo',episode.name).read_bytes()==b'test-video'
    robot=task/'raw'/'episode_20261016_120000_2';robot.mkdir(parents=True)
    c.do_delete_last({'task':'demo','episode':episode.name})
    assert not episode.exists() and robot.exists()
    assert list((task/'.trash').glob('*/'+episode.name+'/video.mp4'))
    c.closed.set()
