"""LAN camera viewer. Only serves images; has no robot control API."""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import io
from pathlib import Path
import threading
import time
from urllib.parse import urlsplit, parse_qs

import cv2
import numpy as np
from cameras import Camera, SERIALS


class Feed:
    def __init__(self, serial):
        self.camera = Camera(serial)
        self.jpeg = None
        self.stamp = 0
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.encode, daemon=True)
        self.thread.start()

    def encode(self):
        while not self.stop.is_set():
            try:
                frame = self.camera.latest()
                ok, jpg = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
                if ok:
                    with self.lock:
                        self.jpeg = jpg.tobytes()
                        self.stamp = self.camera.timestamp
            except RuntimeError:
                pass
            self.stop.wait(.1)

    def snapshot(self):
        with self.lock:
            age = time.monotonic() - self.stamp if self.stamp else None
            return self.jpeg, age


def make_handler(feeds):
    page = Path(__file__).with_name('camera_web.html').read_bytes()
    # A captured frame must have the same timestamp on every HTTP request.
    epoch_offset = time.time() - time.monotonic()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def respond(self, code, mime, body):
            self.send_response(code)
            self.send_header('Content-Type', mime)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def do_GET(self):
            path = urlsplit(self.path).path
            if path == '/':
                return self.respond(200, 'text/html; charset=utf-8', page)
            if path == '/health':
                result = {}
                for serial, feed in feeds.items():
                    jpeg, age = feed.snapshot()
                    result[serial] = {'online': jpeg is not None and age is not None and age < 1,
                                      'age_ms': None if age is None else round(age * 1000)}
                    with feed.camera.lock:
                        times=[t for t,_ in feed.camera.frames]
                    result[serial]['capture_hz']=(len(times)-1)/(times[-1]-times[0]) if len(times)>1 else None
                    result[serial]['frame_intervals_ms']=[round((b-a)*1000,2) for a,b in zip(times,times[1:])]

                return self.respond(200, 'application/json', json.dumps(result).encode())
            if path == '/observation.npz':
                # Lossless BGR frames for the inference client, independent of
                # the 10 FPS JPEG encoder. Shares capture with the web viewer.
                roles = {'high': 'CC1C8100041', 'left': 'CC1C810002Y', 'right': 'CC1C8100015'}
                # Optional cursors select the next unused frame from each bounded queue.
                # Other clients keep the existing latest-frame behavior.
                try:
                    query=parse_qs(urlsplit(self.path).query)
                    after=json.loads(query['after'][0]) if 'after' in query else None
                    if after is not None and (len(after)!=3 or not all(np.isfinite(x) for x in after)):
                        raise ValueError('Invalid frame cursors')
                except (ValueError,TypeError):
                    return self.respond(400,'text/plain',b'Invalid frame cursors')
                deadline=time.monotonic()+.2
                while True:
                    frames, stamps = {}, []
                    for i,(role,serial) in enumerate(roles.items()):
                        camera=feeds[serial].camera
                        with camera.lock:
                            if camera.error or camera.frame is None:
                                return self.respond(503,'text/plain',b'Missing camera')
                            if after is None:
                                item=(camera.timestamp,camera.frame)
                            else:
                                item=next(((t,f) for t,f in camera.frames if epoch_offset+t>after[i]),None)
                            if item is None:break
                            stamp,frame=item
                            if not 0<=time.monotonic()-stamp<.2:
                                return self.respond(503,'text/plain',b'Camera queue overrun or stale frame')
                            frames[role]=frame
                            stamps.append(epoch_offset+stamp)
                    if len(stamps)==3:break
                    if time.monotonic()>=deadline:
                        return self.respond(503,'text/plain',b'Timed out waiting for camera frames')
                    time.sleep(.002)
                if max(stamps) - min(stamps) > .15:
                    return self.respond(503, 'text/plain', b'Camera timestamp skew exceeds 150 ms')
                output = io.BytesIO()
                np.savez(output, **frames, timestamps=np.array(stamps),
                         serials=np.array(list(roles.values())))
                return self.respond(200, 'application/octet-stream', output.getvalue())
            for serial, feed in feeds.items():
                if path == f'/frame/{serial}.jpg':
                    jpeg, age = feed.snapshot()
                    if jpeg is None or age is None or age >= 1:
                        return self.respond(503, 'text/plain', b'Camera unavailable or stale')
                    return self.respond(200, 'image/jpeg', jpeg)
            self.respond(404, 'text/plain', b'Not found')
    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', default='0.0.0.0')
    parser.add_argument('--port', type=int, default=8080)
    args = parser.parse_args()
    feeds = {s: Feed(s) for s in SERIALS}
    try:
        with ThreadingHTTPServer((args.host, args.port), make_handler(feeds)) as server:
            print(f'Camera viewer listening on {args.host}:{args.port}', flush=True)
            server.serve_forever()
    finally:
        for feed in feeds.values():
            feed.stop.set()
            feed.camera.stop.set()
        for feed in feeds.values():
            feed.thread.join(timeout=2)
            feed.camera.thread.join(timeout=2)


if __name__ == '__main__':
    main()
