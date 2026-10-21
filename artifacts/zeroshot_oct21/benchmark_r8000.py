"""Replay saved RGB observations to the LAN server; imports no robot SDK."""
import json,time,uuid
from pathlib import Path
import cv2,numpy as np
from websockets.sync.client import connect
from msgpack_numpy import Packer,unpackb
from action_contract import validate_reset
client=Path(__file__).resolve().parent.parent
row=next(r for r in json.loads((client/'zeroshot_oct21/validation_manifest.json').read_text()) if r['video_id']=='V01')
source=client/'inference_logs/zeroshot_oct21/V01/20261021_095155_009516_execute/initial_observation.npz'
output=client/'inference_logs/r8000_benchmark';output.mkdir(exist_ok=True)
z=np.load(source,allow_pickle=False)
obs={key:cv2.resize(cv2.cvtColor(z[role],cv2.COLOR_BGR2RGB),(320,240)) for key,role in [('observation.images.cam_high','high'),('observation.images.cam_left_wrist','left'),('observation.images.cam_right_wrist','right')]}
state=np.zeros(30,dtype=np.float32);state[:12]=z['state'][:12];obs.update({'observation.state':state,'task':row['prompt']})
report={'note':'Fixed saved observation and synthetic KV replay; no robot control, no task success evaluation.','source':str(source),'records':[]}
packer=Packer()
with connect('ws://192.168.137.223:29056',compression=None,max_size=None,ping_interval=None,open_timeout=15) as ws:
 report['metadata']=unpackb(ws.recv(timeout=30))
 def rpc(kind,request):
  request['_rpc_request_id']=uuid.uuid4().hex
  t=time.perf_counter();payload=packer.pack(request);ws.send(payload);raw=ws.recv(timeout=300)
  if isinstance(raw,str):raise RuntimeError(raw)
  result=unpackb(raw);elapsed=time.perf_counter()-t
  entry={'kind':kind,'total_s':elapsed,'request_bytes':len(payload),'server_timing':result.get('server_timing'),'request_id':request['_rpc_request_id']}
  report['records'].append(entry);print(json.dumps(entry),flush=True)
  (output/'report.json').write_text(json.dumps(report,indent=2))
  return result
 reset=rpc('reset',{'reset':True,'prompt':row['prompt'],'use_icl':True,'icl_latent_path':'/home/huawei/Zero-WAM/outputs/zeroshot_long_oct21/V01.pth','video_guidance_scale':1,'icl_guidance_scale':5})
 validate_reset(reset,'piperpika')
 assert reset['video_inference_steps']==4 and reset['action_inference_steps']==10
 for i in range(3):
  result=rpc('predict',{'obs':obs,'prompt':row['prompt']})
  action=np.asarray(result['action']);assert action.shape==(14,2,16) and np.isfinite(action).all()
  np.save(output/f'action_{i}.npy',action)
  if i<2:rpc('kv',{'obs':[obs]*(4 if i==0 else 8),'state':action,'compute_kv_cache':True})
print('LAN_REPLAY_PASSED',flush=True)
