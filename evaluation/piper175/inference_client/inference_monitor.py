"""Read-only hardware watchdog and atomic UI telemetry. Never issues motor commands."""
import json
import math
from pathlib import Path
import threading
import time
from fault_stop import latch_software_stop


class InferenceMonitor:
    def __init__(self, path, stop_path, probe, interval=.2):
        self.path=Path(path);self.stop_path=Path(stop_path);self.probe=probe;self.interval=interval
        self.lock=threading.RLock();self.done=threading.Event();self.thread=None
        self.state={'phase':'starting','chunk':0,'sent_steps':0,'risk':None,'running':True}

    def update(self, **values):
        with self.lock:self.state.update(values)

    def sample(self):
        try:
            values=self.probe()
            with self.lock:self.state.update(values)
        except Exception as exc:
            self.trip(str(exc))
        self.publish()

    def trip(self, reason):
        # Latch before reporting an alarm; failure propagates, never pretends stopped.
        latch_software_stop(self.stop_path)
        with self.lock:self.state.update(risk=reason,phase='risk_stop_requested')

    def publish(self):
        with self.lock:
            payload={**self.state,'heartbeat':time.time()}
        self.path.parent.mkdir(parents=True,exist_ok=True)
        tmp=self.path.with_suffix('.tmp')
        tmp.write_text(json.dumps(payload,allow_nan=False))
        tmp.replace(self.path)

    def start(self):
        def run():
            while not self.done.is_set():
                try:self.sample()
                except Exception as exc:
                    # A failed telemetry file must also stop future motion.
                    latch_software_stop(self.stop_path)
                    print('Monitor failure: '+str(exc),flush=True)
                    break
                if self.state['risk']:break
                self.done.wait(self.interval)
        self.thread=threading.Thread(target=run,daemon=True);self.thread.start()

    def close(self):
        self.done.set()
        if self.thread:self.thread.join(timeout=2)
        # Do not race a still-running writer.
        if not self.thread or not self.thread.is_alive():
            self.update(running=False,phase='exited');self.publish()


def hardware_probe(piper):
    # check_ready includes the stop gate, feedback freshness, controller faults,
    # joint limits, teaching mode and CAN communication flags.
    from stop_request import StopRequested
    try:
        joints=piper.check_ready()
    except StopRequested:
        return {'stop_requested':True}
    enabled=[list(a.get_joints_enable_status_list()) for a in (piper.robot_l,piper.robot_r)]
    if piper.actuated and not all(all(v) for v in enabled):
        raise RuntimeError('Motor enable feedback lost')
    return {'joint_deg':[math.degrees(float(v)) for v in joints], 'enabled':enabled,
            'hardware_checked_at':time.time(),'stop_requested':False}
