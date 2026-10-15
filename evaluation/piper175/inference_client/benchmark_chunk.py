"""Non-actuating chunk latency benchmark; no robot SDK, no stop-flag changes.
Replays fixed RGB observations/state, with synthetic KV history; timing only.
"""
import argparse
import json
import math
import time
from pathlib import Path
import cv2
import numpy as np
from websockets.sync.client import connect
from msgpack_numpy import Packer, unpackb
from action_diagnostics import summarize_action

p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--observation', type=Path, required=True)
p.add_argument('--output', type=Path, required=True)
p.add_argument('--url', default='ws://127.0.0.1:39056')
p.add_argument('--chunks', type=int, default=3)
p.add_argument('--latent', default='/mnt/sfs_turbo/lijianwen/Datasets/aloha_zerowam_v3/human_latents/run_eval/bread_test000.pth')
a=p.parse_args()
if a.chunks < 1: p.error('chunks must be positive')
a.output.mkdir(parents=True, exist_ok=False)
with np.load(a.observation,allow_pickle=False) as z:
 state=np.zeros(30,dtype=np.float32);state[:12]=z['state'][:12]
 obs={f'observation.images.{role}':cv2.cvtColor(z[key],cv2.COLOR_BGR2RGB)
      for role,key in [('cam_high','high'),('cam_left_wrist','left'),('cam_right_wrist','right')]}
 obs.update({'observation.state':state,'task':'breakfast'})
packer=Packer()
report={'observation':str(a.observation),'mode':'fixed observation replay, synthetic KV history; no actuation',
        'delta_definition':'formatted observation ready -> decoded action response; includes transport and server trace I/O',
        'controller_dt_s':.02,'records':[]}
def save(): (a.output/'report.json').write_text(json.dumps(report,indent=2,default=str))
with connect(a.url,compression=None,max_size=None,ping_interval=None,open_timeout=10,close_timeout=3) as ws:
 report['connect_metadata']=unpackb(ws.recv(timeout=10))
 def rpc(request):
  t0=time.perf_counter();data=packer.pack(request);t1=time.perf_counter()
  ws.send(data);response=ws.recv(timeout=300);t2=time.perf_counter()
  if isinstance(response,str): raise RuntimeError(response)
  result=unpackb(response);t3=time.perf_counter()
  return result,{'total_s':t3-t0,'pack_s':t1-t0,'send_wait_receive_s':t2-t1,'unpack_s':t3-t2,'request_bytes':len(data)}
 try:
  result,timing=rpc({'reset':True,'prompt':'breakfast','use_icl':True,'icl_latent_path':a.latent})
  report['reset_metadata']=result;report['reset_timing']=timing;save()
  for i in range(a.chunks):
   ret,t=rpc({'obs':obs,'prompt':'breakfast'})
   action=np.asarray(ret['action']);np.save(a.output/f'action_{i:03d}.npy',action)
   if action.shape != (28,2,16) or not np.isfinite(action).all():
    raise ValueError(f'Invalid action output: {action.shape}')
   h=int(action.shape[1]*action.shape[2]);valid=h-int(action.shape[2]) if i==0 else h
   row={'chunk':i+1,'first_after_reset':i==0,'shape':list(action.shape),'raw_H':h,'usable_raw_H':valid,
        'infer':t,'d_at_50hz':math.floor(t['total_s']/.02),'delta_over_raw_horizon_50hz':t['total_s']/(h*.02)}
   row['server_diagnostics']=ret.get('diagnostics')
   row['action_diagnostics']=summarize_action(action,first_chunk=i==0,current_joints=state[:12])
   report['records'].append(row);save();print(json.dumps(row),flush=True)
   # Match client keyframe count for the 2x16 output without executing actions.
   if i+1 < a.chunks:
    kr,kt=rpc({'obs':[obs]*(valid//4),'compute_kv_cache':True,'state':action})
    row['kv_server_diagnostics']=kr.get('diagnostics')
    row['kv_timing']=kt;save();print('KV',json.dumps(kt),flush=True)
  ts=np.array([r['infer']['total_s'] for r in report['records']])
  report['summary']={'n':len(ts),'mean_s':float(ts.mean()),'median_s':float(np.median(ts)),
   'min_s':float(ts.min()),'max_s':float(ts.max()),'std_population_s':float(ts.std())}
  if len(ts)>1:report['summary']['subsequent_mean_s']=float(ts[1:].mean())
 finally: save()
print(json.dumps(report['summary']),flush=True)
