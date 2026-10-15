"""Compatibility adapter: lossless frames from the local Piper camera service."""
import io
import os
import time
from urllib.request import urlopen

import numpy as np

SERIALS = ['CC1C8100041', 'CC1C810002Y', 'CC1C8100015']


def decode_observation(payload, now=None):
    with np.load(io.BytesIO(payload), allow_pickle=False) as data:
        stamps = np.asarray(data['timestamps'], dtype=float)
        frames = tuple(data[k].copy() for k in ('high', 'left', 'right'))
        if data['serials'].tolist() != SERIALS:
            raise RuntimeError('Camera role/serial mapping mismatch')
    age = (time.time() if now is None else now) - stamps
    if stamps.shape != (3,) or not np.isfinite(stamps).all() or np.any(age < -.1) or np.any(age > .5):
        raise RuntimeError('Missing/stale camera timestamps')
    if np.ptp(stamps) > .15:
        raise RuntimeError('Camera timestamp skew exceeds 150 ms')
    if any(f.shape != (480, 640, 3) or f.dtype != np.uint8 for f in frames):
        raise RuntimeError('Expected three 640x480 uint8 BGR images')
    return frames, stamps


class CameraManager:
    def __init__(self, cam_high_topic=None, cam_left_topic=None, cam_right_topic=None,
                 width=640, height=480, fps=30):
        if (width, height) != (640, 480):
            raise ValueError('175 camera service uses 640x480')
        self.url = os.environ.get('PIPER_CAMERA_URL', 'http://127.0.0.1:8080').rstrip('/')
        self.timestamps = None

    def start(self):
        deadline = time.monotonic() + 15
        while True:
            try:
                self.get_frames()
                return
            except (OSError, RuntimeError):
                if time.monotonic() >= deadline:
                    raise
                time.sleep(.2)

    def get_frames(self):
        with urlopen(self.url + '/observation.npz', timeout=3) as response:
            payload = response.read()
        frames, self.timestamps = decode_observation(payload)
        return frames  # Original format_obs performs BGR -> RGB exactly once.

    def stop(self):
        pass  # Shared service and web viewer remain running.
