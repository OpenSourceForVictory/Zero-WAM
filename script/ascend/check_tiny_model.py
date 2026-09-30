"""Compare a complete small ICL transformer on CPU and NPU, including caches."""
import copy
import json
import runpy
from pathlib import Path
import torch
import torch_npu
import torch.nn.functional as F
import wan_va.modules.icl_model as mod
from torch.nn.attention.flex_attention import create_block_mask

root=Path(__file__).resolve().parents[2]
helper=runpy.run_path(str(root/'tests/test_icl_model.py'))
torch.manual_seed(17)
torch.set_num_threads(8)
model=mod.WanICLTransformer3DModel(patch_size=(1,1,1),num_attention_heads=2,attention_head_dim=128,in_channels=4,out_channels=4,action_dim=3,text_dim=8,freq_dim=4,ffn_dim=256,num_layers=2,rope_max_seq_len=32,action_inner_dim=256,action_ffn_dim=256,attn_window=4,enable_mcp=False).to(dtype=torch.bfloat16).eval()
npu=copy.deepcopy(model).to('npu:0')
orig_apply=mod.ICLAttentionBackend.apply
orig_builder=mod._compiled_create_block_mask

def uncompiled(*args,**kwargs):
 kwargs['_compile']=False
 return create_block_mask(*args,**kwargs)

def reference(cls,q,k,v,*,cross_attention,block_mask=None):
 if q.device.type=='npu':
  return orig_apply(q,k,v,cross_attention=cross_attention,block_mask=block_mask)
 mask=block_mask
 qi=torch.arange(q.shape[1])[:,None]
 ki=torch.arange(k.shape[1])[None,:]
 allow=mask.mask_mod(torch.tensor(0),torch.tensor(0),qi,ki)
 return F.scaled_dot_product_attention(q.float().transpose(1,2),k.float().transpose(1,2),v.float().transpose(1,2),attn_mask=allow).transpose(1,2).to(v.dtype)

mod.ICLAttentionBackend.apply=classmethod(reference)
mod._compiled_create_block_mask=uncompiled

def move(obj):
 if torch.is_tensor(obj): return obj.to('npu:0')
 if isinstance(obj,dict): return {k:move(v) for k,v in obj.items()}
 return obj

checks=[]
with torch.inference_mode():
 for mode,cache in [('video',2),('video',1),('action',1)]:
  data=helper['_input'](model,mode,cache)
  kwargs=dict(update_cache=int(mode=='video'),cache_name='pos',mode='forward_latent_only' if mode=='video' else 'forward_action_only')
  expected=model(data,**kwargs)
  actual=npu(move(data),**kwargs).cpu()
  error=(actual.float()-expected.float()).abs()
  torch.testing.assert_close(actual,expected,atol=0.04,rtol=0.04)
  result=dict(mode=mode,cache_type=cache,max_abs_error=error.max().item(),mean_abs_error=error.mean().item(),shape=list(actual.shape))
  checks.append(result)
  print(result,flush=True)
(root/'logs/tiny-model-comparison.json').write_text(json.dumps(checks,indent=2)+'\n')
