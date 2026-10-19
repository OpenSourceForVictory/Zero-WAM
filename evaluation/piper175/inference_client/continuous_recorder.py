"""Independent three-camera MJPEG recorder; never opens CAN or camera devices."""
import argparse
import csv
import json
from pathlib import Path
import signal
import time

import cv2
from camera_manager import CameraManager


def record(output, stop_file, fps=30, segment_seconds=30, duration=None):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    running = True
    def stop(*_):
        nonlocal running
        running = False
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    camera = CameraManager()
    writers = {}
    counts = dict.fromkeys(('high', 'left', 'right'), 0)
    last = dict.fromkeys(counts, 0.0)
    started = time.monotonic()
    segment = -1
    error = None
    try:
        camera.start()
        with (output / 'timestamps.csv').open('w') as file:
            rows = csv.writer(file)
            rows.writerow(['role', 'segment', 'frame', 'capture_epoch_s', 'write_epoch_s'])
            while running and not Path(stop_file).exists():
                tick = time.monotonic()
                if duration is not None and tick-started >= duration:
                    break
                frames = camera.get_frames()
                current_segment = int((tick-started) // segment_seconds)
                if current_segment != segment:
                    for writer in writers.values(): writer.release()
                    writers = {}
                    segment = current_segment
                    for role, frame in zip(counts, frames):
                        h, w = frame.shape[:2]
                        writer = cv2.VideoWriter(str(output / f'{role}_{segment:05d}.avi'), cv2.VideoWriter_fourcc(*'MJPG'), fps, (w,h))
                        if not writer.isOpened(): raise RuntimeError(f'Cannot open video writer: {role}')
                        writers[role] = writer
                    (output / 'ready.json').write_text(json.dumps({'fps':fps, 'codec':'MJPG', 'segment_seconds':segment_seconds, 'started_epoch_s':time.time()}))
                    segment_counts = dict.fromkeys(counts, 0)
                for role, frame, stamp in zip(counts, frames, camera.timestamps):
                    if stamp <= last[role]: continue
                    writers[role].write(frame)
                    rows.writerow([role, segment, segment_counts[role], float(stamp), time.time()])
                    counts[role] += 1
                    segment_counts[role] += 1
                    last[role] = stamp
                file.flush()
                time.sleep(max(0, 1/fps - (time.monotonic()-tick)))
    except BaseException as exc:
        error = repr(exc)
        raise
    finally:
        for writer in writers.values(): writer.release()
        (output / 'result.json').write_text(json.dumps({'frames':counts, 'duration_s':time.monotonic()-started, 'error':error}, indent=2))


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--output', required=True)
    p.add_argument('--stop-file', required=True)
    p.add_argument('--duration', type=float)
    a = p.parse_args()
    record(a.output, a.stop_file, duration=a.duration)
