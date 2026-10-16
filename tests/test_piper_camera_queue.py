"""HTTP frame cursors use retained frames, independently for each reader."""
from collections import deque
from types import SimpleNamespace
import io
import json
from pathlib import Path
import sys
import threading
import time
from urllib.request import urlopen
from urllib.parse import urlencode
import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'evaluation/piper175'))
import camera_web


def test_cursor_pairs_at_common_time_and_does_not_consume_other_readers():
    now=time.monotonic();feeds={}
    for serial in camera_web.SERIALS:
        frames=deque([(now-.04,np.full((2,2,3),1,np.uint8)),(now-.02,np.full((2,2,3),2,np.uint8)),(now,np.full((2,2,3),3,np.uint8))])
        camera=SimpleNamespace(lock=threading.Lock(),frames=frames,timestamp=now,frame=frames[-1][1],error=None)
        feeds[serial]=SimpleNamespace(camera=camera)
    server=camera_web.ThreadingHTTPServer(('127.0.0.1',0),camera_web.make_handler(feeds))
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        base=f'http://127.0.0.1:{server.server_port}/observation.npz'
        def read(query=''):
            with urlopen(base+query) as r:return dict(np.load(io.BytesIO(r.read())))
        latest=read();assert (latest['high']==3).all()
        query='?'+urlencode({'after':json.dumps((latest['timestamps']-.03).tolist())})
        first=read(query);second=read(query)
        assert (first['high']==3).all()
        assert np.array_equal(first['timestamps'],second['timestamps'])
        assert len(feeds[camera_web.SERIALS[0]].camera.frames)==3
    finally:server.shutdown();server.server_close();thread.join()


def test_common_time_recovers_pairing_after_one_camera_drops_frames():
    queues=[[(t,str(t)) for t in times] for times in
            ([1.,1.03,1.06,1.09,1.12,1.15], [1.,1.15], [1.01,1.04,1.07,1.10,1.13,1.16])]
    pair=camera_web.pair_frames(queues,[1.,1.,1.01])
    assert [x[0] for x in pair]==[1.15,1.15,1.16]
    assert camera_web.pair_frames(queues,[1.15,1.15,1.16]) is None
    assert camera_web.pair_frames([[(1.,None)],[(1.3,None)],[(1.4,None)]],[0,0,0]) is None


def test_high_endpoint_is_independent_of_wrist_cameras():
    now=time.monotonic()
    camera=SimpleNamespace(lock=threading.Lock(),frames=deque([(now,np.ones((4,4,3),np.uint8))]),error=None)
    feeds={'CC1C8100041':SimpleNamespace(camera=camera)}
    server=camera_web.ThreadingHTTPServer(('127.0.0.1',0),camera_web.make_handler(feeds))
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        url=f'http://127.0.0.1:{server.server_port}/camera/high.npz'
        with urlopen(url) as r:first=dict(np.load(io.BytesIO(r.read())))
        with urlopen(url) as r:second=dict(np.load(io.BytesIO(r.read())))
        assert set(first)=={'high','timestamp'}
        assert first['timestamp']==second['timestamp']
        assert len(camera.frames)==1
    finally:server.shutdown();server.server_close();thread.join()
