"""Check the local single-NPU WebSocket inference endpoint."""
import json
from pathlib import Path
import time
import urllib.request
import numpy as np
import websockets.sync.client
from infer_npu import read_observation
from wan_va.configs.va_robotwin_cfg import va_robotwin_cfg
from wan_va.utils.Simple_Remote_Infer.deploy.msgpack_numpy import Packer,unpackb
root=Path(__file__).resolve().parents[1]
for attempt in range(120):
 try:
  with urllib.request.urlopen('http://127.0.0.1:29856/healthz',timeout=2) as response:
   assert response.status==200
  break
 except OSError: time.sleep(1)
else: raise RuntimeError('Server did not become healthy')
pack=Packer()
with websockets.sync.client.connect('ws://127.0.0.1:29856',compression=None,max_size=None,ping_interval=None) as conn:
 metadata=unpackb(conn.recv())
 def call(obj):
  conn.send(pack.pack(obj))
  result=conn.recv(timeout=120)
  if isinstance(result,str): raise RuntimeError(result)
  return unpackb(result)
 reset=call(dict(reset=True,prompt='Use one arm to grab the object and put it on the scale.',use_icl=True,icl_latent_path=str(root/'data/place_object_scale_icl.pth'),video_guidance_scale=-1,icl_guidance_scale=5))
 result=call(dict(obs=[read_observation(root/'data/place_object_scale',va_robotwin_cfg.obs_cam_keys)]))
 action=result['action']
 assert action.shape==(16,2,16) and np.isfinite(action).all()
 report=dict(health_status=200,reset_ok=True,action_shape=list(action.shape),finite_actions=True,server_timing=result['server_timing'])
(root/'logs/server-probe.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps(report,indent=2))
