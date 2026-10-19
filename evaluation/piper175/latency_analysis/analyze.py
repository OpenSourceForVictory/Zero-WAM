import json,csv
from pathlib import Path
import numpy as np
root=Path(__file__).resolve().parent
runs=json.loads((root/'runs_20261019.json').read_text())
def stats(a):
 a=np.asarray(a,dtype=float)
 return dict(n=len(a),mean=round(float(a.mean()),3),median=round(float(np.median(a)),3),p95=round(float(np.percentile(a,95)),3),min=round(float(a.min()),3),max=round(float(a.max()),3)) if len(a) else {}
def mode(r):
 if 'video4_action10' in r['folder']:return '320x240_v4_a10'
 if 'half_resolution' in r['folder']:return '320x240_v20_a20'
 return '640x480_v20_a20'+('_train' if 'full_recorded' in r['folder'] else '')
groups={};per_run=[]
for r in runs:
 ps=[x for x in r['rpc'] if x['kind']=='predict'];ks=[x for x in r['rpc'] if x['kind']=='kv'];r['p']=ps;r['k']=ks
 groups.setdefault(mode(r),[]).append(r)
 row=dict(run=r['run'],mode=mode(r),pred_count=len(ps),kv_count=len(ks),first_pred=ps[0]['total_s'],first_kv=ks[0]['total_s'],later_pred=stats([x['total_s'] for x in ps[1:]]),later_kv=stats([x['total_s'] for x in ks[1:]]))
 per_run.append(row)
print('PER_RUN')
for r in per_run: print(json.dumps(r))
summary={}
for name,rs in groups.items():
 summary[name]={}
 for phase in ('first','later'):
  s={}
  for kind in ('p','k'):
   values=[x['total_s'] for r in rs for x in (r[kind][:1] if phase=='first' else r[kind][1:])]
   s[kind]=stats(values)
  pairs=[r['p'][i]['total_s']+r['k'][i]['total_s'] for r in rs for i in (range(min(1,len(r['k']))) if phase=='first' else range(1,min(len(r['p']),len(r['k']))))]
  s['paired_wait']=stats(pairs)
  summary[name][phase]=s
 print(name,json.dumps(summary[name]))
print('MATCHED_CHUNKS_2_TO_17')
matched={}
for name,rs in groups.items():
 if name.endswith('_train'):continue
 matched[name]={k:stats([x['total_s'] for r in rs for x in r[k][1:17]]) for k in ('p','k')}
 matched[name]['paired_wait']=stats([r['p'][i]['total_s']+r['k'][i]['total_s'] for r in rs for i in range(1,17)])
 print(name,json.dumps(matched[name]))
print('TRANSPORT')
for name,rs in groups.items():
 for kind in ('p','k'):
  a=[x for r in rs for x in r[kind][1:]]
  print(name,kind,'request_bytes',sorted(set(x['request_bytes'] for x in a)), 'pack_ms',round(np.mean([x['pack_s']*1000 for x in a]),3),'unpack_ms',round(np.mean([x['unpack_s']*1000 for x in a]),3))
(root/'summary.json').write_text(json.dumps(dict(per_run=per_run,summary=summary,matched_chunks_2_to_17=matched),indent=2))
with (root/'per_chunk.csv').open('w',newline='') as f:
 fields=['run','mode','chunk','phase','predict_s','kv_s','paired_wait_s','predict_request_bytes','kv_request_bytes']
 w=csv.DictWriter(f,fieldnames=fields);w.writeheader()
 for r in runs:
  for i,p in enumerate(r['p']):
   k=r['k'][i] if i<len(r['k']) else None
   w.writerow(dict(run=r['run'],mode=mode(r),chunk=i+1,phase='first' if i==0 else 'later',predict_s=p['total_s'],kv_s=k['total_s'] if k else '',paired_wait_s=p['total_s']+k['total_s'] if k else '',predict_request_bytes=p['request_bytes'],kv_request_bytes=k['request_bytes'] if k else ''))
