"""Three DC1 views, using V4L2 and Tk. No ROS or robot connection."""
from collections import deque
import argparse
import base64
from pathlib import Path
import threading
import time
import cv2

SERIALS = ('CC1C8100015', 'CC1C810002Y', 'CC1C8100041')
# Installation orientation, keyed by stable serial rather than /dev/videoN.
ROTATION_DEGREES = {'CC1C8100015': 0, 'CC1C810002Y': 0, 'CC1C8100041': 0}


class Camera:
    def __init__(self, serial):
        self.serial = serial
        self.path = f'/dev/v4l/by-id/usb-Sonix_Technology_Co.__Ltd._Dabai_DC1_{serial}-video-index0'
        self.frames = deque(maxlen=12)
        self.frame = None
        self.timestamp = 0
        self.error = None
        self.stop = threading.Event()
        self.lock = threading.Lock()
        self.thread = threading.Thread(target=self.read, daemon=True)
        self.thread.start()

    def read(self):
        while not self.stop.is_set():
            self.read_once()
            self.stop.wait(2)

    def read_once(self):
        cap = cv2.VideoCapture(self.path, cv2.CAP_V4L2)
        try:
            if not cap.isOpened():
                raise RuntimeError(f'Cannot open {self.path}')
            cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*'MJPG'))
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
            cap.set(cv2.CAP_PROP_FPS, 30)
            # Keep enough V4L2 buffers queued while MJPEG decoding runs.
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 4)
            while not self.stop.is_set():
                ok, frame = cap.read()
                if not ok:
                    raise RuntimeError('Camera read failed')
                if ROTATION_DEGREES[self.serial] == 180:
                    frame = cv2.rotate(frame, cv2.ROTATE_180)
                with self.lock:
                    self.frame, self.timestamp = frame, time.monotonic()
                    self.frames.append((self.timestamp, frame))
                    self.error = None
        except Exception as exc:
            self.error = str(exc)
        finally:
            cap.release()

    def latest(self):
        with self.lock:
            if self.error or self.frame is None or time.monotonic() - self.timestamp > 1:
                raise RuntimeError(self.error or f'{self.serial}: waiting for fresh frame')
            return self.frame.copy()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshot', type=Path, help='Save three PNGs without opening a window')
    args = parser.parse_args()
    cameras = [Camera(s) for s in SERIALS]
    try:
        if args.snapshot:
            args.snapshot.mkdir(parents=True, exist_ok=False)
            deadline = time.monotonic() + 15
            while True:
                try:
                    frames = [c.latest() for c in cameras]
                    break
                except RuntimeError:
                    if time.monotonic() > deadline:
                        raise
                    time.sleep(.1)
            for c, frame in zip(cameras, frames):
                path = args.snapshot / f'{c.serial}.png'
                if not cv2.imwrite(str(path), frame):
                    raise IOError(str(path))
                print(path, frame.shape)
        else:
            import tkinter as tk
            root = tk.Tk()
            root.title('Piper 175 — DC1 cameras (Q / Esc to close)')
            labels = []
            for i, c in enumerate(cameras):
                panel = tk.Frame(root)
                panel.grid(row=0, column=i)
                tk.Label(panel, text=c.serial).pack()
                label = tk.Label(panel, text='Opening camera...', width=60)
                label.pack()
                labels.append(label)
            def tick():
                for c, label in zip(cameras, labels):
                    try:
                        frame = cv2.resize(c.latest(), (480, 360))
                        ok, png = cv2.imencode('.png', frame)
                        if not ok:
                            raise RuntimeError('PNG encoding failed')
                        photo = tk.PhotoImage(data=base64.b64encode(png).decode())
                        label.configure(image=photo, text='', width=480)
                        label.photo = photo
                    except RuntimeError as exc:
                        label.configure(image='', text=str(exc), width=60)
                root.after(100, tick)
            root.bind('<Escape>', lambda _: root.destroy())
            root.bind('q', lambda _: root.destroy())
            tick()
            root.mainloop()
    finally:
        for c in cameras:
            c.stop.set()
        for c in cameras:
            c.thread.join(timeout=2)


if __name__ == '__main__':
    main()
