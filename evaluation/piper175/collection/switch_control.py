"""One Sense, two Piper arms: explicit selection and continuous dual recording.

serve is read-only unless --execute. No enable/reset/zero commands.
Uses original FK/IK with a fresh node/service namespace on every selection.
"""
import argparse
import fcntl
import json
import math
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import threading
import time

from collect import channels, check, conflicting_processes
from core import load_profile
from switch_model import Selection, encoded_target, sent_target

ROOT=Path(__file__).resolve().parent
SOCKET=ROOT/'.switch.sock'


def read_arm(arm, require_enabled):
    j=arm.GetArmJointMsgs(); g=arm.GetArmGripperMsgs(); s=arm.GetArmStatus()
    for label,msg in (('joints',j),('gripper',g),('status',s)):
        stamp=msg.time_stamp
        age=time.time()-stamp
        if not 0<=age<.3: raise RuntimeError(f'Stale CAN {label} feedback: age={age:.3f}s')
    status=s.arm_status
    if int(status.arm_status)!=0 or int(status.teach_status)!=0 or status.err_code:
        raise RuntimeError('Arm fault or teaching mode: '+str(status))
    if require_enabled:
        low=arm.GetArmLowSpdInfoMsgs()
        if int(status.ctrl_mode)!=1: raise RuntimeError('Arm must already be in CAN control mode')
        if not all(getattr(low,f'motor_{i}').foc_status.driver_enable_status for i in range(1,7)):
            raise RuntimeError('Both arms must already be enabled')
    flags=g.gripper_state.foc_status
    for name in ('voltage_too_low','motor_overheating','driver_overcurrent','driver_overheating','sensor_status','driver_error_status'):
        if getattr(flags,name): raise RuntimeError('Gripper fault: '+name)
    q=[math.radians(getattr(j.joint_state,f'joint_{i}')/1000) for i in range(1,7)]
    q.append(g.gripper_state.grippers_angle/1e6)
    if not all(math.isfinite(x) for x in q) or not 0<=q[6]<=.08:
        raise RuntimeError('Invalid feedback')
    return q


class Runtime:
    def __init__(self,execute):
        import rclpy
        from sensor_msgs.msg import JointState
        from geometry_msgs.msg import PoseStamped
        from data_msgs.msg import Gripper
        from std_msgs.msg import String
        self.ros=rclpy; self.JointState=JointState; self.String=String
        rclpy.init(); self.node=rclpy.create_node('pika_single_sense_dual_gateway')
        self.execute=execute; self.lock=threading.RLock(); self.selection=Selection()
        self.arms={}; self.targets={}; self.feedback={}; self.error=None
        self.pose_at=0.; self.sense_at=0.; self.grip=None
        self.child=None; self.child_log=None; self.source_pub=None; self.dynamic=[]
        self.ik_at=0.; self.ready=False; self.running=True
        self.dest=ROOT/'acceptance'/time.strftime('dual_switch_%Y%m%d_%H%M%S'); self.dest.mkdir()
        self.events=(self.dest/'events.jsonl').open('a',buffering=1)
        self.state_pub={side:self.node.create_publisher(JointState,'/pika_switch/state_'+suffix,10) for side,suffix in [('left','l'),('right','r')]}
        self.action_pub={side:self.node.create_publisher(JointState,'/pika_switch/action_'+suffix,10) for side,suffix in [('left','l'),('right','r')]}
        self.mode_pub=self.node.create_publisher(String,'/pika_switch/mode',10)
        self.node.create_subscription(PoseStamped,'/pika_pose_l',self.pose,10)
        self.node.create_subscription(Gripper,'/sensor/gripper_l/data',self.sense,10)
        self.thread=None

    def event(self,kind,**kw):
        self.events.write(json.dumps({'time':time.time(),'kind':kind,**kw})+'\n')

    def pose(self,msg):
        q=msg.pose.orientation; p=msg.pose.position
        values=[p.x,p.y,p.z,q.x,q.y,q.z,q.w]
        stamp=msg.header.stamp.sec+msg.header.stamp.nanosec/1e9
        if all(math.isfinite(x) for x in values) and abs(sum(x*x for x in values[3:])-1)<.05 and -.02<=time.time()-stamp<.2:
            with self.lock:self.pose_at=time.monotonic()

    def sense(self,msg):
        # Original sensor bridge flags clamped >1.67 rad as error. Only that
        # documented endpoint saturation is accepted; other errors stay invalid.
        stamp=msg.header.stamp.sec+msg.header.stamp.nanosec/1e9
        saturated=msg.error and abs(msg.angle-1.67)<1e-5 and abs(msg.distance-.09670704)<1e-5
        if (not msg.error or saturated) and math.isfinite(msg.distance) and -.02<=time.time()-stamp<.2:
            with self.lock:
                self.grip=min(.08,max(0.,msg.distance)); self.sense_at=time.monotonic()

    def js(self,q):
        msg=self.JointState(); msg.header.stamp=self.node.get_clock().now().to_msg()
        msg.position=list(q); return msg

    def status(self):
        with self.lock:
            return {'execute':self.execute,'phase':self.selection.phase,'selected_arm':self.selection.side,
                    'ik_age_s':time.monotonic()-self.ik_at if self.ik_at else None,'frontend_ready':self.ready,
                    'active_arm':self.selection.side if self.selection.phase=='active' else None,
                    'generation':self.selection.generation,'error':self.error,'log':str(self.dest)}

    def pause(self):
        with self.lock:
            self.selection.pause()
            # Explicit new holding targets, never substitute state for action data.
            for side,arm in self.arms.items():
                self.targets[side]=read_arm(arm,self.execute)
            if self.execute and not self.error:
                self.send_targets()
            self.event('pause',targets=self.targets)

    def send_targets(self):
        for side,arm in self.arms.items():
            joints,grip=encoded_target(self.targets[side])
            arm.MotionCtrl_2(0x01,0x01,30)
            arm.JointCtrl(*joints)
            arm.GripperCtrl(grip,1000,0x01,0)
            self.action_pub[side].publish(self.js(sent_target(self.targets[side])))

    def tick(self):
        with self.lock:
            try:
                for side,arm in self.arms.items():
                    self.feedback[side]=read_arm(arm,self.execute)
                    self.state_pub[side].publish(self.js(self.feedback[side]))
                if self.source_pub and self.selection.side:
                    self.source_pub.publish(self.js(self.feedback[self.selection.side]))
                if self.selection.phase=='active':
                    now=time.monotonic()
                    if now-self.pose_at>.2 or now-self.sense_at>.2 or now-self.ik_at>.3 or not self.child or self.child.poll() is not None:
                        self.pause(); self.event('input_lost_pause')
                if self.execute and not self.error:
                    self.send_targets()
            except Exception as exc:
                if not self.error:self.event('fault',error=repr(exc))
                self.error=repr(exc); self.selection.phase='fault'; self.selection.side=None
            self.mode_pub.publish(self.String(data=json.dumps(self.status())))

    def kill_frontend(self):
        if self.child and self.child.poll() is None:
            os.killpg(self.child.pid,signal.SIGINT)
            try:self.child.wait(timeout=3)
            except subprocess.TimeoutExpired:
                os.killpg(self.child.pid,signal.SIGTERM)
                try:self.child.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    os.killpg(self.child.pid,signal.SIGKILL); self.child.wait(timeout=2)
        self.child=None
        self.ready=False;self.ik_at=0.
        if self.child_log:self.child_log.close(); self.child_log=None

    def select(self,side,preview=False):
        from data_msgs.msg import TeleopStatus
        if not self.execute and not preview: raise RuntimeError('Read-only server; use preview-left/right to check IK without motion')
        if self.execute and preview: raise RuntimeError('Preview requires a read-only server')
        if self.error: raise RuntimeError('Fault is latched; inspect and restart')
        self.pause(); self.kill_frontend()
        with self.lock:
            if time.monotonic()-self.pose_at>.2 or time.monotonic()-self.sense_at>.2:
                raise RuntimeError('Sense/Tracker input missing or stale')
            generation=self.selection.prepare(side); index='_switch'+str(generation)
            self.ik_at=0.;self.ready=False;self.frontend_started_at=time.time()
            if self.source_pub is None:
                self.source_pub=self.node.create_publisher(self.JointState,'/pika_switch/source_feedback',10)
                self.dynamic.append(('publisher',self.source_pub))
                def target(msg):
                    with self.lock:
                        if len(msg.position)<6 or not all(math.isfinite(x) for x in msg.position[:6]):return
                        stamp=msg.header.stamp.sec+msg.header.stamp.nanosec/1e9
                        # Reject queued targets from the destroyed frontend.
                        if stamp<self.frontend_started_at or not -.02<=time.time()-stamp<.2:return
                        self.ik_at=time.monotonic()
                        if self.selection.phase=='active' and self.selection.side:
                            self.targets[self.selection.side]=list(msg.position[:6])+[self.grip]
                def ready(msg):
                    with self.lock:self.ready=not msg.fail and not msg.quit
                self.dynamic.append(('subscription',self.node.create_subscription(self.JointState,'/pika_switch/ik_target',target,10)))
                self.dynamic.append(('subscription',self.node.create_subscription(TeleopStatus,'/pika_switch/frontend_status',ready,10)))
        env=os.environ.copy();env['PIKA_SWITCH_INDEX']=index
        self.child_log=(self.dest/f'frontend_{generation}_{side}.log').open('w')
        self.child=subprocess.Popen(['ros2','launch',str(ROOT/'switch_ik.launch.py')],env=env,
                                    stdout=self.child_log,stderr=subprocess.STDOUT,start_new_session=True)
        subprocess.run(['ros2','service','call','/teleop_trigger'+index,'std_srvs/srv/Trigger','{}'],
                       stdout=self.child_log,stderr=subprocess.STDOUT,timeout=15,check=True)
        deadline=time.monotonic()+15
        while time.monotonic()<deadline:
            with self.lock:
                ready_now=self.ready and time.monotonic()-self.ik_at<.2
            if ready_now:break
            if self.child.poll() is not None:raise RuntimeError('Frontend exited')
            time.sleep(.05)
        else:raise RuntimeError('Frontend did not establish a fresh relative pose and IK target')
        with self.lock:
            if self.error:raise RuntimeError(self.error)
            self.selection.phase='preview' if preview else 'active';self.event('selected',arm=side,generation=generation,preview=preview)
        return self.status()

    def open(self,p):
        from piper_sdk import C_PiperInterface
        for side,ch in zip(('left','right'),channels(p)):
            arm=C_PiperInterface(can_name=ch); self.arms[side]=arm;arm.ConnectPort(piper_init=False)
        time.sleep(.5)
        for side,arm in self.arms.items():
            self.feedback[side]=read_arm(arm,self.execute);self.targets[side]=self.feedback[side][:]
        self.event('startup',**self.status(),feedback=self.feedback)
        self.timer=self.node.create_timer(.02,self.tick)
        self.thread=threading.Thread(target=self.ros.spin,args=(self.node,),daemon=True);self.thread.start()

    def close(self):
        # Gate frontend before process teardown; no disable/reset/zero.
        try:
            if self.thread:self.pause()
        finally:
            self.kill_frontend()
            if self.thread:
                self.ros.shutdown();self.thread.join(timeout=3)
            else:self.ros.shutdown()
            for arm in self.arms.values():arm.DisconnectPort()
            self.node.destroy_node();self.events.close()


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('command',choices=['serve','left','right','preview-left','preview-right','pause','status','stop'])
    ap.add_argument('--execute',action='store_true');args=ap.parse_args()
    if args.command!='serve':
        with socket.socket(socket.AF_UNIX) as sock:
            sock.settimeout(45);sock.connect(str(SOCKET));sock.sendall((args.command+'\n').encode())
            chunks=[]
            while True:
                b=sock.recv(65536)
                if not b:break
                chunks.append(b)
            result=json.loads(b''.join(chunks));print(json.dumps(result,indent=2))
            if 'command_error' in result:raise SystemExit(2)
        return
    p=load_profile(ROOT/'profile_single_sense_dual.json')
    lock=(ROOT/'.teleop.lock').open('w');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    if conflicting_processes():raise RuntimeError('Inference/recovery is running')
    # Process executable/argv inspection avoids matching this SSH command text.
    for proc in Path('/proc').glob('[0-9]*'):
        try:argv=(proc/'cmdline').read_bytes().split(b'\0')
        except OSError:continue
        if any(Path(os.fsdecode(x)).name in ('piper_single_ctrl','gripper_only_probe.py') for x in argv[:3] if x):
            raise RuntimeError('Another CAN controller is running')
    if not check(p):raise RuntimeError('Preflight failed')
    runtime=Runtime(args.execute)
    def stop(*_):runtime.running=False
    signal.signal(signal.SIGINT,stop);signal.signal(signal.SIGTERM,stop)
    try:
        runtime.open(p)
        SOCKET.unlink(missing_ok=True)
        with socket.socket(socket.AF_UNIX) as server:
            server.bind(str(SOCKET));os.chmod(SOCKET,0o600);server.listen(4);server.settimeout(.5)
            print(json.dumps(runtime.status()),flush=True)
            while runtime.running:
                try:conn,_=server.accept()
                except socket.timeout:continue
                with conn:
                    conn.settimeout(2)
                    try:
                        cmd=conn.recv(32).decode().strip()
                        if cmd in ('left','right'):runtime.select(cmd)
                        elif cmd in ('preview-left','preview-right'):runtime.select(cmd.split('-')[1],preview=True)
                        elif cmd in ('pause','stop'):
                            runtime.pause();runtime.kill_frontend()
                            if cmd=='stop':runtime.running=False
                        elif cmd!='status':raise ValueError('Unknown command')
                        result=runtime.status()
                    except Exception as exc:
                        try:runtime.pause();runtime.kill_frontend()
                        except Exception:pass
                        result={**runtime.status(),'command_error':repr(exc)}
                    conn.sendall(json.dumps(result).encode())
    finally:
        runtime.close();SOCKET.unlink(missing_ok=True)


if __name__=='__main__':main()
