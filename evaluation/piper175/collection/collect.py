#!/usr/bin/env python3
"""Read-only ROS + HTTP recorder; launch subcommands are explicitly separate."""
import argparse
from collections import deque
import hashlib
import importlib
import fcntl
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import threading
import time
from urllib.request import urlopen

import numpy as np
from core import load_profile, decode_cameras, valid_message, build_index

ROOT = Path(__file__).resolve().parent


def write_json(path, value):
    path = Path(path)
    tmp = path.with_suffix(path.suffix+'.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n')
    tmp.replace(path)


def camera(p):
    with urlopen(p['camera_url']+'/observation.npz', timeout=2) as r:
        payload = r.read()
    return decode_cameras(payload, p, time.time())


def channels(p):
    result = []
    for port in p['can_usb_ports']:
        matches = [x.name for x in Path('/sys/class/net').iterdir()
                   if (x/'device').resolve().name == port]
        if len(matches)!=1:
            raise RuntimeError(f'Expected one CAN device at {port}, found {matches}')
        result.append(matches[0])
    return result


def conflicting_processes():
    lines = subprocess.check_output(['ps','-eo','pid,args'], text=True).splitlines()
    names = ('real_robot_inference_merged.py','recover_and_zero.sh','go_zero.py','recover_control.py')
    return [s.strip() for s in lines if any(n in s for n in names)
            and 'sshd:' not in s and 'bash -c' not in s]


def check(p):
    failures = []
    def item(label, fn):
        try:
            value = fn()
            if value is False: raise RuntimeError('not available')
            print(f'OK {label}: {value}')
        except Exception as e:
            failures.append(label); print(f'FAIL {label}: {e}')
    for module in ('rclpy','sensor_msgs.msg','geometry_msgs.msg','data_msgs.msg',
                   'piper_sdk','pinocchio','casadi','meshcat','cv2','h5py'):
        item('import '+module,lambda module=module: bool(importlib.import_module(module)))
    for port in p['serial_ports']:
        item(port, lambda port=port: Path(port).exists() and os.access(port,os.R_OK|os.W_OK))
    item('CAN mapping',lambda: channels(p))
    def can_up():
        devices = json.loads(subprocess.check_output(['ip','-j','-d','link','show','type','can']))
        found = {x['ifname']:x for x in devices}
        for name in channels(p):
            x = found[name]
            if 'UP' not in x['flags'] or x['linkinfo']['info_data']['bittiming']['bitrate']!=1000000:
                raise RuntimeError(f'{name} must be UP at 1 Mbps; no change was made')
        return True
    item('CAN state',can_up)
    item('libsurvive calibration file',lambda: (Path.home()/'.config/libsurvive/config.json').is_file())
    item('camera serials/timestamps',lambda: camera(p)[1].tolist())
    manifest = ROOT/'legacy_manifest.json'
    def verify():
        hashes = json.loads(manifest.read_text())
        for rel, expected in hashes.items():
            path=Path(p['legacy_root'])/rel
            if hashlib.sha256(path.read_bytes()).hexdigest()!=expected:
                raise RuntimeError(f'Legacy dependency changed: {path}')
        return f'{len(hashes)} frozen dependency hashes matched'
    item('legacy dependency hashes',verify)
    item('control ownership',lambda: not conflicting_processes())
    print('CHECK ONLY: no device opened for control, no CAN activation or enable command.')
    return not failures


class Observer:
    def __init__(self,p):
        import rclpy
        from rclpy.signals import SignalHandlerOptions
        from rclpy.qos import QoSProfile,ReliabilityPolicy,DurabilityPolicy
        from rosidl_runtime_py.utilities import get_message
        from rosidl_runtime_py.convert import message_to_ordereddict
        rclpy.init(signal_handler_options=SignalHandlerOptions.NO)
        self.ros=rclpy
        self.node=rclpy.create_node('pika175_collection_observer')
        self.rows={k:deque(maxlen=2000) for k in {**p['required_topics'],**p['optional_topics']}}
        self.lock=threading.Lock(); self.output=None; self.error=None
        qos=QoSProfile(depth=100,reliability=ReliabilityPolicy.BEST_EFFORT,
                       durability=DurabilityPolicy.VOLATILE)
        def callback(key):
            def receive(msg):
                try:
                    now=time.time(); d=message_to_ordereddict(msg)
                    stamp=d.get('header',{}).get('stamp')
                    source=stamp['sec']+stamp['nanosec']/1e9 if stamp else now
                    row={'stream':key,'source_time':source,'receive_time':now,
                         'receive_monotonic':time.monotonic(),'message':d}
                    with self.lock:
                        self.rows[key].append(row)
                        if self.output:
                            self.output.write(json.dumps(row,allow_nan=False)+'\n')
                except Exception as e:
                    self.error=repr(e)
            return receive
        for key,(topic,typ) in {**p['required_topics'],**p['optional_topics']}.items():
            self.node.create_subscription(get_message(typ),topic,callback(key),qos)
        self.thread=threading.Thread(target=self.spin,daemon=True); self.thread.start()

    def spin(self):
        try: self.ros.spin(self.node)
        except Exception as e: self.error=repr(e)

    def readiness(self,p,keys=None):
        now=time.time(); issues=[]
        with self.lock:
            for key in keys or p['required_topics']:
                topic=({**p['required_topics'],**p['optional_topics']})[key][0]
                if self.node.count_publishers(topic)!=1:
                    issues.append(f'{key}: expected exactly one publisher')
                row=self.rows[key][-1] if self.rows[key] else None
                if row is None or not valid_message(row):
                    issues.append(f'{key}: missing/invalid message')
                elif not (0<=now-row['receive_time']<=p['max_age'] and
                          -.02<=now-row['source_time']<=p['max_age']):
                    issues.append(f'{key}: stale source/receive timestamp')
        if self.error: issues.append(self.error)
        return issues

    def close(self):
        self.ros.shutdown(); self.thread.join(timeout=3); self.node.destroy_node()


def record(p,args):
    if conflicting_processes(): raise RuntimeError('Inference/recovery controller is running')
    if args.seconds<0: raise ValueError('--seconds must be >= 0')
    if not args.task.strip(): raise ValueError('Task instruction must not be empty')
    base=Path(args.output or p['data_root']).expanduser()
    base.mkdir(parents=True,exist_ok=True)
    if shutil.disk_usage(base).free < p['min_free_gb']*1e9: raise RuntimeError('Insufficient disk space')
    obs=Observer(p); stop=threading.Event(); old={}
    episode=None; meta=None; frames_file=None; messages_file=None
    try:
        deadline=time.monotonic()+8
        while True:
            issues=obs.readiness(p)
            if not issues: break
            if time.monotonic()>=deadline: raise RuntimeError('; '.join(issues))
            time.sleep(.1)
        camera(p)
        # A new exclusive directory for every take; no overwrite of an old episode.
        episode=base/(time.strftime('episode_%Y%m%d_%H%M%S')+'_'+str(os.getpid()))
        episode.mkdir(); (episode/'frames').mkdir()
        meta={'schema':'pika175-v1','profile':p,'task':args.task,'status':'recording',
              'started_at':time.time(),'image_encoding':'BGR uint8, lossless NPZ',
              'vector_order':'left joints 1..6, right joints 1..6, left gripper, right gripper',
              'units':'joint targets/feedback radians; gripper targets/feedback meters',
              'action_semantics':'ROS requested joint/gripper targets before legacy driver conversion; NOT measured state',
              'legacy_hashes':json.loads((ROOT/'legacy_manifest.json').read_text()),
              'calibration_sha256':hashlib.sha256((Path.home()/'.config/libsurvive/config.json').read_bytes()).hexdigest()
                  if (Path.home()/'.config/libsurvive/config.json').exists() else None,
              'collection_hashes':{x.name:hashlib.sha256(x.read_bytes()).hexdigest()
                                   for x in ROOT.iterdir() if x.suffix in ('.py','.sh','.json')},
              'software':{name:{'version':getattr(importlib.import_module(name),'__version__',None),
                                'file':getattr(importlib.import_module(name),'__file__',None)}
                          for name in ('numpy','rclpy','piper_sdk')},
              'hardware_validation':'pending onsite validation; stream checks do not prove safe motion'}
        write_json(episode/'manifest.json',meta)
        messages_file=(episode/'messages.jsonl').open('w',buffering=1)
        frames_file=(episode/'frames.jsonl').open('w',buffering=1)
        with obs.lock:
            # Preserve pre-roll for causal alignment of first image.
            for queue in obs.rows.values():
                for row in queue: messages_file.write(json.dumps(row,allow_nan=False)+'\n')
            obs.output=messages_file
        for sig in (signal.SIGINT,signal.SIGTERM):
            old[sig]=signal.signal(sig,lambda *_:stop.set())
        print(f'Recording {episode}; Ctrl+C ends ONLY recording, not teleoperation.',flush=True)
        start=time.monotonic(); next_tick=start; n=0; last_stamps=None
        while not stop.is_set() and (args.seconds<=0 or time.monotonic()-start<args.seconds):
            issues=obs.readiness(p)
            if issues: raise RuntimeError('; '.join(issues))
            if shutil.disk_usage(base).free < p['min_free_gb']*1e9: raise RuntimeError('Low disk space')
            images,stamps=camera(p)
            if last_stamps is not None and np.any(stamps<=last_stamps):
                raise RuntimeError('Repeated/non-increasing camera frame timestamp')
            name=f'frames/{n:06d}.npz'
            np.savez(episode/name,**images,timestamps=stamps)
            frames_file.write(json.dumps({'file':name,'source_times':stamps.tolist(),
                                          'receive_time':time.time()})+'\n')
            n+=1; last_stamps=stamps
            if n%int(max(1,p['hz']))==0: print(f'{n} frames, elapsed {time.monotonic()-start:.1f}s',flush=True)
            next_tick+=1/p['hz']; stop.wait(max(0,next_tick-time.monotonic()))
        meta['status']='complete'
    except BaseException as e:
        if meta: meta.update(status='failed',error=repr(e))
        raise
    finally:
        with obs.lock: obs.output=None
        obs.close()
        for sig,handler in old.items(): signal.signal(sig,handler)
        for f in (frames_file,messages_file):
            if f: f.flush(); os.fsync(f.fileno()); f.close()
        if meta:
            meta['finished_at']=time.time(); write_json(episode/'manifest.json',meta)
            print(f'Saved {episode}: {meta["status"]}. Recorder sends no robot commands.')
    return validate(episode)


def validate(episode):
    aligned,report=build_index(episode)
    episode=Path(episode)
    # Verify every referenced image archive, even frames rejected for time alignment.
    for line in (episode/'frames.jsonl').read_text().splitlines():
        f=json.loads(line)
        meta=json.loads((episode/'manifest.json').read_text())
        path=episode/f['file']
        if not path.resolve().is_relative_to(episode.resolve()): raise ValueError('Invalid frame path')
        with np.load(path,allow_pickle=False) as z:
            for role in meta['profile']['camera_roles']:
                if z[role].shape!=(480,640,3) or z[role].dtype!=np.uint8: raise ValueError('Corrupt image shape')
            if z['timestamps'].tolist()!=f['source_times']: raise ValueError('Timestamp manifest mismatch')
    write_json(episode/'quality.json',report)
    with (episode/'aligned.jsonl').open('w') as f:
        for row in aligned: f.write(json.dumps(row,allow_nan=False)+'\n')
    print(json.dumps(report,ensure_ascii=False,indent=2))
    return report['valid']


def export(episode):
    import h5py
    if not validate(episode): raise RuntimeError('Quality check failed; refusing HDF5 export')
    episode=Path(episode); aligned,_=build_index(episode)
    dest=episode/'data.hdf5'
    if dest.exists(): raise FileExistsError(dest)
    tmp=episode/'data.hdf5.tmp'
    with h5py.File(tmp,'x') as f:
        f.attrs['schema']='pika175-v1; images are relative NPZ paths, preserve raw directory'
        f.attrs['color_order']='BGR'
        f.attrs['vector_order']='left6,right6,left_gripper,right_gripper; rad,m'
        f.create_dataset('timestamp',data=[r['time'] for r in aligned])
        f.create_dataset('observations/qpos',data=[r['state'] for r in aligned])
        f.create_dataset('action',data=[r['action'] for r in aligned])
        f.create_dataset('observations/image_archives',data=[r['frame'] for r in aligned],dtype=h5py.string_dtype())
        f.attrs['manifest']=(episode/'manifest.json').read_text()
    tmp.rename(dest)
    print(dest)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile',default=str(ROOT/'profile.json'))
    subs=parser.add_subparsers(dest='command',required=True)
    subs.add_parser('check')
    subs.add_parser('streams')
    for name in ('sensors','teleop'):
        sub=subs.add_parser(name); sub.add_argument('--execute',action='store_true')
    sub=subs.add_parser('record'); sub.add_argument('--task',required=True)
    sub.add_argument('--seconds',type=float,default=0); sub.add_argument('--output')
    for name in ('validate','export'):
        sub=subs.add_parser(name); sub.add_argument('episode')
    args=parser.parse_args(); p=load_profile(args.profile)
    if args.command=='check': return 0 if check(p) else 2
    if args.command in ('validate','export'):
        if args.command=='validate': return 0 if validate(args.episode) else 2
        export(args.episode); return 0
    if args.command=='record': return 0 if record(p,args) else 2
    if args.command=='streams':
        obs=Observer(p)
        try:
            time.sleep(3); issues=obs.readiness(p); print('\n'.join(issues) or 'All required streams fresh')
            return 2 if issues else 0
        finally: obs.close()
    if args.command in ('sensors','teleop'):
        if not args.execute:
            print(f'Plan only: {args.command}. Add --execute to start. No processes started.'); return 0
        lock=open(ROOT/f'.{args.command}.lock','w')
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        os.set_inheritable(lock.fileno(),True)
        existing=subprocess.check_output(['ps','-eo','args'],text=True)
        signatures=('piper_single_ctrl','piper_IK.py') if args.command=='teleop' else ('pika_double_locator_node','serial_gripper_imu')
        if any(x in existing for x in signatures):
            raise RuntimeError('An existing legacy sensor/controller process is running')
        if not check(p): raise RuntimeError('Preflight failed; no launch started')
        if args.command=='teleop':
            obs=Observer(p)
            try:
                time.sleep(3); issues=obs.readiness(p,keys=['pika_left','pika_right','sense_left','sense_right'])
                if issues: raise RuntimeError('; '.join(issues))
            finally: obs.close()
        os.environ['PIKA175_PROFILE']=str(Path(args.profile).resolve())
        os.execvp('ros2',['ros2','launch',str(ROOT/'pipeline.launch.py'),f'mode:={args.command}'])


if __name__=='__main__':
    raise SystemExit(main())
