"""Opt-in trace wrapper around the original ICL server; no model math changes."""
import os,json,time,random,hashlib,re,traceback,copy
from pathlib import Path
import numpy as np
import torch
from PIL import Image
from wan_va import wan_va_server as server
ROOT=Path(os.environ['ZERO_WAM_TRACE_ROOT']);ROOT.mkdir(parents=True,exist_ok=True)
C=server.VA_Server
original_infer=C.infer
original_post=C.postprocess_action
original_load=C._load_or_encode_icl
original_read=C._read_icl_video
original_encode=C._encode_initial_obs

def cpu(x):return x.detach().cpu().clone()
def dump(path,data):path.write_text(json.dumps(data,indent=2,default=str))
def active(s):return getattr(s,'_trace_dir',None)

def read(self,path):
    v=original_read(self,path)
    if active(self):
        # These frames show actual resized/sampled model input; latents saved exactly separately.
        np.save(active(self)/'demo_sampled_rgb_uint8.npy',v[0].permute(1,2,3,0).clamp(0,255).round().byte().cpu().numpy())
    return v

def load(self,*args,**kwargs):
    result=original_load(self,*args,**kwargs)
    if active(self):torch.save({'latent':cpu(result[0]),'text_emb':None if result[1] is None else cpu(result[1])},active(self)/'demo_latent.pt')
    return result

def encode(self,obs):
    fn=self.streaming_vae.encode_chunk
    def capture(v):
        if active(self):torch.save(cpu(v),active(self)/'observation_preprocessed.pt')
        return fn(v)
    self.streaming_vae.encode_chunk=capture
    try:
        initial=original_encode(self,obs)
        if self._oracle_input:
            future=np.load(self._oracle_input);videos=[]
            for key in self.job_config.obs_cam_keys:
                v=torch.from_numpy(future[key].copy()).to(self.device).float().permute(0,3,1,2)/255*2-1
                v=torch.nn.functional.interpolate(v,size=(self.height,self.width),mode='bilinear',align_corners=False)
                videos.append(v.permute(1,0,2,3).unsqueeze(0))
            encoded=fn(torch.cat(videos).to(self.vae.dtype));mu,_=torch.chunk(encoded,2,dim=1)
            mean=torch.tensor(self.vae.config.latents_mean).to(mu.device);std=torch.tensor(self.vae.config.latents_std).to(mu.device)
            mu=self.normalize_latents(mu,mean,1/std)
            future_latent=torch.cat(mu.split(1,dim=0),dim=-1).to(self.device)
            assert future_latent.shape[2]==1
            self._oracle_latent=torch.cat([initial,future_latent],dim=2)
            torch.save(cpu(self._oracle_latent),active(self)/'oracle_future_latent.pt')
        return initial
    finally:self.streaming_vae.encode_chunk=fn

def post(self,a):
    if active(self):torch.save(cpu(a),active(self)/'action_normalized.pt')
    out=original_post(self,a)
    if active(self):np.save(active(self)/'action_returned.npy',out)
    return out

@torch.no_grad()
def infer(self,obs):
    if obs.get('reset'):
        name=obs.get('trace_name')
        self._trace_dir=None
        if name:
            if not re.fullmatch(r'[a-zA-Z0-9_-]{1,90}',name):raise ValueError('Invalid trace_name')
            self._trace_dir=ROOT/name
            self._trace_dir.mkdir(exist_ok=False)
            dump(active(self)/'reset_request.json',obs)
        # Explicit offline intervention: never enabled by normal requests.
        self._oracle_input=obs.get('diagnostic_future_path')
        if self._oracle_input and not active(self):
            raise ValueError('Future-video intervention requires a recorded run')
        self.job_config.diagnostic_oracle_future=bool(self._oracle_input)
        self._oracle_latent=None
        result=original_infer(self,obs)
        if active(self):
            cfg=dict(self.job_config)
            dump(active(self)/'config.json',cfg)
            torch.save({'prompt':cpu(self.prompt_embeds),'negative':cpu(self.negative_prompt_embeds),'target':cpu(self.target_prompt_embeds)},active(self)/'text_embeddings.pt')
            np.savez(active(self)/'normalization.npz',q01=self.actions_q01.numpy(),q99=self.actions_q99.numpy(),used=np.asarray(self.job_config.used_action_channel_ids))
            src=Path(server.__file__)
            dump(active(self)/'provenance.json',{'server_source':str(src),'server_sha256':hashlib.sha256(src.read_bytes()).hexdigest(),'model_path':self.job_config.model_path,'torch':torch.__version__,'numpy':np.__version__,'dtype':str(self.dtype),'camera_order':self.job_config.obs_cam_keys,'first_valid_action_index':[1,0],'note':'Trace records one chunk per reset; no hardware commands. Diffusion iterates are noisy intermediate samples, not executable commands.'})
        return result
    if 'diagnostic_seed' in obs:
        seed=int(obs['diagnostic_seed']);random.seed(seed);np.random.seed(seed);torch.manual_seed(seed)
        if hasattr(torch,'npu'):torch.npu.manual_seed_all(seed)
    if not active(self) or obs.get('compute_kv_cache'):return original_infer(self,obs)
    d=active(self);images=obs['obs'];images=images[0] if isinstance(images,list) else images
    np.savez(d/'observation.npz',**images)
    for k,v in images.items():Image.fromarray(v).save(d/(k+'.png'))
    dump(d/'infer_request.json',{k:v for k,v in obs.items() if k!='obs'})
    traces={'video':[],'action':[]};old=[]
    for name,sched in [('video',self.scheduler),('action',self.action_scheduler)]:
        fn=sched.step;old.append((sched,fn))
        def step(noise,timestep,sample,*args,_fn=fn,_name=name,**kwargs):
            entry={'timestep':float(timestep),'sample_before':cpu(sample),'predicted_flow':cpu(noise)}
            result=_fn(noise,timestep,sample,*args,**kwargs)
            if _name=='video' and len(traces[_name])==len(self.scheduler.timesteps)-1 and self._oracle_latent is not None:
                entry['model_sample_after']=cpu(result)
                result=self._oracle_latent.clone()
                entry['oracle_injected']=True
            entry['sample_after']=cpu(result);traces[_name].append(entry)
            return result
        sched.step=step
    t=time.monotonic()
    try:
        result=original_infer(self,obs)
        torch.save({'initial':cpu(self.init_latent),'predicted':cpu(self.last_predicted_latents),'cached_actions':cpu(self.last_predicted_actions)},d/'final_latents.pt')
        dump(d/'completion.json',{'success':True,'seconds':time.monotonic()-t,'video_steps':len(traces['video']),'action_steps':len(traces['action']),'seed':obs.get('diagnostic_seed'),'chunk_idx':self.chunk_idx})
        return result
    except Exception:
        (d/'error.txt').write_text(traceback.format_exc());raise
    finally:
        for sched,fn in old:sched.step=fn
        torch.save(traces,d/'denoising_trace.pt')
        self._trace_dir=None

C._read_icl_video=read;C._load_or_encode_icl=load;C._encode_initial_obs=encode;C.postprocess_action=post;C.infer=infer
def run():
    server.init_logger()
    server.main()

if __name__ == "__main__":
    run()
