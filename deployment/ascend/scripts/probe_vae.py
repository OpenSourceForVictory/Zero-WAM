"""Probe official Wan VAE encode/decode on the Ascend device."""
import hashlib
import json
from pathlib import Path
import time
import torch
import torch_npu
import torch.nn.functional as F
import imageio.v2 as imageio
from wan_va.modules.utils import load_vae, WanVAEStreamingWrapper
root=Path(__file__).resolve().parents[1]
p=root/'models/zero-wam-posttrain-robotwin/vae/diffusion_pytorch_model.safetensors'
while not p.exists(): time.sleep(10)
h=hashlib.sha256()
with p.open('rb') as f:
 while part:=f.read(8*1024*1024): h.update(part)
assert h.hexdigest()=='62cd18f19438e35b32ac63020e2852f566e9b02f46b6cdbd87972a356e3c6f4b'
torch.set_num_threads(8)
torch.npu.set_device(0)
vae=load_vae(str(p.parent),torch.bfloat16,'npu:0').eval()
wrapper=WanVAEStreamingWrapper(vae)
frames=[]
for cam in ['cam_high','cam_left_wrist','cam_right_wrist']:
 image=imageio.imread(root/f'data/place_object_scale/observation.images.{cam}.png')
 t=torch.from_numpy(image).permute(2,0,1).float()[None]/255*2-1
 frames.append(F.interpolate(t,size=(224,288),mode='bilinear',align_corners=False))
x=torch.cat(frames).unsqueeze(2).to('npu:0',dtype=torch.bfloat16)
with torch.inference_mode():
 started=time.monotonic()
 encoded=wrapper.encode_chunk(x)
 mu,_=encoded.chunk(2,dim=1)
 print('ENCODE',tuple(encoded.shape),torch.isfinite(encoded).all().item(),flush=True)
 history=x.repeat(1,1,4,1,1)
 next_encoded=wrapper.encode_chunk(history)
 next_mu,_=next_encoded.chunk(2,dim=1)
 assert next_mu.shape[2] == 1
 decoded=vae.decode(torch.cat([mu,next_mu],dim=2),return_dict=False)[0]
 torch.npu.synchronize()
 assert torch.isfinite(encoded).all() and torch.isfinite(decoded).all()
 report=dict(encoded_shape=list(encoded.shape),decoded_shape=list(decoded.shape),finite=True,seconds=time.monotonic()-started,max_memory_allocated_gib=torch.npu.max_memory_allocated()/1024**3)
 print(json.dumps(report),flush=True)
(root/'logs/vae-probe.json').write_text(json.dumps(report,indent=2)+'\n')
