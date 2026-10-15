"""Bounded Tracker pose check. Starts only the locator, never arm/serial nodes."""
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import time


def run(p, seconds=20, side='both'):
    import rclpy
    from geometry_msgs.msg import PoseStamped
    from rclpy.qos import qos_profile_sensor_data
    root=Path(__file__).resolve().parent/'acceptance'/time.strftime('locator_%Y%m%d_%H%M%S')
    root.mkdir(parents=True,exist_ok=False)
    existing=subprocess.check_output(['ps','-eo','args'],text=True)
    if 'pika_double_locator_node' in existing or 'survive-cli' in existing:
        raise RuntimeError('Existing locator/calibration process; refusing a second USB reader')
    if not 1 <= seconds <= 300 or side not in ('left', 'right', 'both'):
        raise ValueError('Invalid duration or side')
    from inspect_usb import snapshot
    (root/'usb_before.json').write_text(json.dumps(snapshot(), indent=2))
    selected = ('left', 'right') if side == 'both' else (side,)
    counts={'left':0,'right':0}; last={}; valid={'left':0,'right':0}
    arrivals = {'left': [], 'right': []}
    rclpy.init(); node=rclpy.create_node('pika175_locator_probe'); child=None
    def receive(side):
        def callback(msg):
            stamp=msg.header.stamp.sec+msg.header.stamp.nanosec/1e9
            pos=msg.pose.position; q=msg.pose.orientation
            values=[pos.x,pos.y,pos.z,q.x,q.y,q.z,q.w]
            counts[side]+=1
            arrivals[side].append(time.monotonic())
            good=all(math.isfinite(x) for x in values) and abs(sum(x*x for x in values[3:])-1)<.05 and -.02<=time.time()-stamp<.2
            if good: valid[side]+=1
            last[side]={'source_time':stamp,'receive_time':time.time(),'pose':values,'valid':good}
        return callback
    for side,suffix in [('left','l'),('right','r')]:
        node.create_subscription(PoseStamped,'/pika_pose_'+suffix,receive(side),qos_profile_sensor_data)
    executable=Path(p['legacy_root'])/'install/pika_locator/lib/pika_locator/pika_double_locator_node'
    args=[str(executable),'--ros-args','-p','left_hand_code:='+p['tracker_codes'][0],
          '-p','right_hand_code:='+p['tracker_codes'][1],'-p','publish_rate:=100.0']
    early_exit=None; passed=False
    try:
        with (root/'locator.log').open('w') as log:
            child=subprocess.Popen(args,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
            started=time.monotonic()
            deadline=started+seconds
            while time.monotonic()<deadline and child.poll() is None:
                rclpy.spin_once(node,timeout_sec=.1)
            early_exit=child.poll()
            ended=time.monotonic()
            passed=early_exit is None and all(valid[k]>=20 and last[k]['valid']
                and time.time()-last[k]['receive_time']<.2 for k in selected)
    finally:
        if child and child.poll() is None:
            os.killpg(child.pid,signal.SIGINT)
            try:child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid,signal.SIGTERM)
                try:child.wait(timeout=3)
                except subprocess.TimeoutExpired:os.killpg(child.pid,signal.SIGKILL);child.wait()
        node.destroy_node()
        if rclpy.ok():rclpy.shutdown()
    metrics = {}
    for key, times in arrivals.items():
        metrics[key] = {
            'mean_hz_during_probe': len(times)/(ended-started),
            'first_frame_delay_s': times[0]-started if times else None,
            'max_interframe_gap_s': max((b-a for a,b in zip(times,times[1:])), default=None),
            'end_silence_s': ended-times[-1] if times else ended-started,
        }
    (root/'usb_after.json').write_text(json.dumps(snapshot(), indent=2))
    result={'counts':counts,'valid_counts':valid,'last':last,'log_dir':str(root),
            'requested_seconds':seconds,'elapsed_seconds':ended-started,
            'expected_side':side,'metrics':metrics,
            'scope':'basic pose output only; not accuracy or stability acceptance',
            'early_exit_code':early_exit,
            'passed':passed}
    (root/'result.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))
    return result['passed']
