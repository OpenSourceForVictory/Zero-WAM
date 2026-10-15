"""ROS transport/recorder test using synthetic topics and real read-only HTTP images.
No robot SDK is imported. Unique topic names cannot reach normal robot controllers.
Run with the same ROS/Pika Python environment as run.sh.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import uuid

ROOT=Path(__file__).resolve().parent
os.environ['ROS_LOCALHOST_ONLY']='1'
os.environ['ROS_DOMAIN_ID']='173'
import rclpy
from sensor_msgs.msg import JointState
from geometry_msgs.msg import PoseStamped


def main():
    p=json.loads((ROOT/'profile.json').read_text())
    ident='synthetic_'+uuid.uuid4().hex
    out=ROOT/'acceptance'/ident; out.mkdir(parents=True)
    for key,v in {**p['required_topics'],**p['optional_topics']}.items():
        v[0]=f'/{ident}/{key}'
    profile=out/'profile.json';profile.write_text(json.dumps(p))
    rclpy.init();node=rclpy.create_node(ident)
    pubs={k:node.create_publisher(PoseStamped if k.startswith('pika_') else JointState,v[0],10)
          for k,v in p['required_topics'].items()}
    stop=threading.Event()
    def publish():
        while not stop.is_set():
            for key,pub in pubs.items():
                if key.startswith('pika_'):
                    msg=PoseStamped();msg.pose.orientation.w=1.
                else:
                    msg=JointState();msg.position=[.02 if key.startswith('action_') else .01]*6+[.01]
                msg.header.stamp=node.get_clock().now().to_msg();pub.publish(msg)
            stop.wait(.01)
    thread=threading.Thread(target=publish);thread.start()
    try:
        child=subprocess.run(['bash',str(ROOT/'run.sh'),'--profile',str(profile),'record',
                              '--task','SYNTHETIC SOFTWARE TEST - not robot demonstration',
                              '--seconds','3','--output',str(out)],timeout=25)
        if child.returncode: return child.returncode
        episodes=list(out.glob('episode_*'))
        if len(episodes)!=1: raise RuntimeError('Expected one isolated test episode')
        return subprocess.run(['bash',str(ROOT/'run.sh'),'export',str(episodes[0])],timeout=25).returncode
    finally:
        stop.set();thread.join();node.destroy_node();rclpy.shutdown()


if __name__=='__main__': raise SystemExit(main())
