"""Reset-only transport smoke test. Does not import or connect to robot hardware."""
import json
import sys
import time
import uuid
from pathlib import Path
from websockets.sync.client import connect

sys.path.insert(0, str(Path.cwd() / 'wan_va/utils/Simple_Remote_Infer'))
from deploy.msgpack_numpy import Packer, unpackb

rid=uuid.uuid4().hex
request={'reset':True,'prompt':'pick object to the container',
         'icl_latent_path':'/mnt/sfs_turbo/lijianwen/Datasets/piperpika_10_16_zerowam_joint_v2/human_latents/run_eval/group_002.pth',
         'video_guidance_scale':1,'icl_guidance_scale':5,'_rpc_request_id':rid}
with connect('ws://127.0.0.1:29056',compression=None,ping_interval=None) as ws:
    ws.recv(timeout=10)
    t0=time.perf_counter();data=Packer().pack(request);t1=time.perf_counter()
    ws.send(data);reply=ws.recv(timeout=120);t2=time.perf_counter()
    if isinstance(reply,str):raise RuntimeError(reply)
    response=unpackb(reply);t3=time.perf_counter()
assert response['server_timing']['request_id']==rid
assert (response['video_inference_steps'],response['action_inference_steps'])==(4,10)
print(json.dumps(dict(request_id=rid,kind='reset',total_s=t3-t0,pack_s=t1-t0,unpack_s=t3-t2,
                     send_wait_receive_s=t2-t1,server_timing=response['server_timing'],
                     video_steps=response['video_inference_steps'],action_steps=response['action_inference_steps'])))
