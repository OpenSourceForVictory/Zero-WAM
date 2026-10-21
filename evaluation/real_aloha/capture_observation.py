"""Capture fresh ROS RGB observations only. Does not import a robot SDK or send commands."""
import time,json,threading
from pathlib import Path
from datetime import datetime
import rospy,numpy as np,cv2
from sensor_msgs.msg import Image
from cv_bridge import CvBridge
import argparse
ap=argparse.ArgumentParser();ap.add_argument('--output',required=True);args=ap.parse_args()
out=Path(args.output);out.mkdir(parents=True,exist_ok=False)
rospy.init_node('zero_wam_readonly_sequence',anonymous=True,disable_signals=True)
bridge=CvBridge();lock=threading.Lock();buffers={}
def cb(msg,key):
 with lock:buffers.setdefault(key,[]).append(msg)
subs=[]
for n,k in [('f','cam_high'),('l','cam_left_wrist'),('r','cam_right_wrist')]:
 subs.append(rospy.Subscriber('/camera_'+n+'/color/image_raw',Image,cb,callback_args=k,queue_size=10,buff_size=2**25))
start=time.time();time.sleep(3)
for s in subs:s.unregister()
with lock:data=dict(buffers)
required=['cam_high','cam_left_wrist','cam_right_wrist']
assert set(data)==set(required),str(data.keys())
def stamp(m):return m.header.stamp.to_sec()
for k in required:
 data[k]=[m for m in data[k] if start<=stamp(m)<=time.time()]
 assert len(data[k])>=5,(k,len(data[k]))
t0=max(stamp(data[k][0]) for k in required)+.15
rows=[];frames={k:[] for k in required[:3]}
for i in range(5):
 target=t0+i/15;selected={k:min(data[k],key=lambda m:abs(stamp(m)-target)) for k in required}
 stamps={k:stamp(m) for k,m in selected.items()}
 assert max(abs(v-target) for v in stamps.values())<.05
 assert max(stamps.values())-min(stamps.values())<.08
 for k in frames:
  im=bridge.imgmsg_to_cv2(selected[k],'rgb8');frames[k].append(im)
  cv2.imwrite(str(out/(k+'_%02d.png'%i)),cv2.cvtColor(im,cv2.COLOR_RGB2BGR))
 rows.append({'index':i,'target_time':target,'timestamps':stamps})
obs={'observation.images.'+k:v[0] for k,v in frames.items()};future={'observation.images.'+k:np.stack(v[1:]) for k,v in frames.items()}
np.savez_compressed(out/'observation.npz',**obs);np.savez_compressed(out/'future.npz',**future)
meta={'capture_time':datetime.now().astimezone().isoformat(),'physical_commands':0,'source':'Live ROS RGB subscriptions only; robot pose is not collected by this script. Capture pose separately before physical evaluation.','samples':rows}
(out/'manifest.json').write_text(json.dumps(meta,indent=2));print(json.dumps(meta))
