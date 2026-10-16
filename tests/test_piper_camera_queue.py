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


def test_cursor_retains_intermediate_frames_and_does_not_consume_other_readers():
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
        assert (first['high']==2).all()
        assert np.array_equal(first['timestamps'],second['timestamps'])
        assert len(feeds[camera_web.SERIALS[0]].camera.frames)==3
    finally:server.shutdown();server.server_close();thread.join()
