"""Lossless observation snapshots plus a three-view inspection video."""

import json
import time
import numpy as np

CAMERAS = ("cam_high", "cam_left_wrist", "cam_right_wrist")


def json_value(value):
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    raise TypeError(type(value).__name__)


def write_json(path, value):
    path.write_text(
        json.dumps(value, default=json_value, indent=2, allow_nan=False) + "\n"
    )


class Recorder:
    def __init__(self, output, video=True):
        self.output = output
        self.count = 0
        self.writer = None
        self.video = video

    def observe(self, observation):
        for name in CAMERAS:
            frame = observation[name]
            if (
                not isinstance(frame, np.ndarray)
                or frame.dtype != np.uint8
                or frame.ndim != 3
                or frame.shape[2] != 3
            ):
                raise ValueError("Expected RGB uint8: " + name)
        stamp = time.time()
        name = "observation_%04d.npz" % self.count
        np.savez_compressed(
            self.output / name,
            timestamp=stamp,
            **{
                k: observation[k]
                for k in (*CAMERAS, "states", "eef_positions", "eef_quaternions_wxyz")
            },
        )
        if self.video:
            import imageio.v2 as imageio

            if self.writer is None:
                self.writer = imageio.get_writer(
                    str(self.output / "observations.mp4"), format="FFMPEG", fps=5
                )
            self.writer.append_data(
                np.concatenate([observation[k] for k in CAMERAS], axis=1)
            )
        self.count += 1
        return dict(file=name, timestamp=stamp)

    def close(self):
        if self.writer is not None:
            self.writer.close()
