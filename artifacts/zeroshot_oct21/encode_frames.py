"""Read-only inference conditioning: same sampling/normalization as Oct19, no training."""
import json,sys,time
from pathlib import Path
import numpy as np
import torch,torch_npu
sys.path.insert(0,'/mnt/sfs_turbo/lijianwen/Codes/Zero-WAM')
from wan_va.modules.utils import load_vae,load_text_encoder,load_tokenizer,WanVAEStreamingWrapper
from diffusers.pipelines.wan.pipeline_wan import prompt_clean
out=Path(__file__).resolve().parent;rows=json.loads((out/'validation_manifest.json').read_text());model=Path('/mnt/sfs_turbo/lijianwen/Codes/Zero-WAM/outputs/piperpika_checkpoint_eval_final/best_model');device='npu:0';dtype=torch.bfloat16;torch.set_num_threads(4)
tok=load_tokenizer(str(model/'tokenizer'));enc=load_text_encoder(str(model/'text_encoder'),dtype,device).eval();texts={}
with torch.inference_mode():
 for r in rows:
  x=tok([prompt_clean(r['prompt'])],padding='max_length',max_length=512,truncation=True,return_tensors='pt');t=enc(x.input_ids.to(device),x.attention_mask.to(device)).last_hidden_state[0];t[int(x.attention_mask.sum()):]=0;texts[r['video_id']]=t.cpu()
del enc;torch.npu.empty_cache();vae=load_vae(str(model/'vae'),dtype,device).eval();wrapper=WanVAEStreamingWrapper(vae);mean=torch.tensor(vae.config.latents_mean,device=device).view(1,-1,1,1,1);std=torch.tensor(vae.config.latents_std,device=device).view(1,-1,1,1,1)
with torch.inference_mode():
 for r in rows:
  deadline=time.monotonic()+600
  while not (out/(r['video_id']+'_frames.npz')).exists():
   if time.monotonic()>deadline:raise TimeoutError('Sampled frames not uploaded: '+r['video_id'])
   time.sleep(1)
  dest=out/(r['video_id']+'.pth');assert not dest.exists();wrapper.clear_cache();a=np.load(out/(r['video_id']+'_frames.npz'));encoded=[];batch=[]
  for rgb in a['frames']:
   frame=torch.from_numpy(rgb.copy()).permute(2,0,1).float();frame=torch.nn.functional.interpolate(frame[None],size=(320,480),mode='bilinear',align_corners=False)[0];batch.append(frame)
   if len(batch)==(1 if not encoded else 4):
    video=(torch.stack(batch,dim=1)[None]/255*2-1).to(device=device,dtype=dtype);mu=wrapper.encode_chunk(video).chunk(2,dim=1)[0];encoded.append(((mu-mean)/std).to(dtype).cpu());batch=[]
  latent=torch.cat(encoded,dim=2)[0];c,f,h,w=latent.shape;assert c==48 and f==(len(a['frames'])-1)//4+1 and torch.isfinite(latent).all();tmp=dest.with_suffix('.tmp');torch.save(dict(latent=latent.permute(1,2,3,0).reshape(-1,c).contiguous(),latent_num_frames=f,latent_height=h,latent_width=w,frame_ids=a['frame_ids'].tolist(),text_emb=texts[r['video_id']],source_video=r['server_video'],prompt=r['prompt']),tmp);tmp.replace(dest);print('ENCODED',r['video_id'],list(latent.shape),flush=True)
(out/'ENCODING_SUCCESS').write_text('12 distinct video/prompt inputs encoded; no model training.\n')
