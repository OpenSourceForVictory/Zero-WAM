"""LAN RTX8000 entry; preserves the current client's action and recording logic."""
import argparse,json,os,subprocess,fcntl,sys
from pathlib import Path
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('video_id',choices=[f'V{i:02d}' for i in range(1,13)])
p.add_argument('--mode',choices=['check','shadow','execute'],default='check')
p.add_argument('--max-chunks',type=int,default=1)
a=p.parse_args()
if a.max_chunks<0:p.error('max-chunks must be nonnegative')
here=Path(__file__).resolve().parent;client=here.parent
locks=[]
for path in (client.parent/'.recover_and_zero.lock',client.parent/'collection/.relocalize.lock'):
 f=path.open('a');fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB);locks.append(f)
sys.path.insert(0,str(client.parent/'collection'))
from enable_zero import controllers
if controllers():raise RuntimeError('Another robot controller is running; stop it before switching servers')
row=next(r for r in json.loads((here/'validation_manifest.json').read_text()) if r['video_id']==a.video_id)
row=dict(row);row['prompt']='pick object to container'
env=os.environ.copy();env.update(ZERO_WAM_ICL_LATENT_PATH='/home/huawei/Zero-WAM/outputs/zeroshot_long_oct21/training_prompt/'+a.video_id+'.pth',ZERO_WAM_VIDEO_GUIDANCE_SCALE='1',ZERO_WAM_ICL_GUIDANCE_SCALE='5');env.pop('ZERO_WAM_ICL_ID',None)
cmd=['bash',str(client/'run.sh'),'--'+a.mode,'--action-contract','piperpika','--prompt',row['prompt'],'--server_host','192.168.137.223','--server_port','29056','--max_chunks',str(a.max_chunks),'--save_dir',str(client/'inference_logs/zeroshot_r8000_oct21'/a.video_id)]
if a.mode!='check':cmd.append('--record-continuous')
print(json.dumps({'video_id':a.video_id,'prompt':row['prompt'],'latent':env['ZERO_WAM_ICL_LATENT_PATH'],'endpoint':'ws://192.168.137.223:29056'},ensure_ascii=False),flush=True)
raise SystemExit(subprocess.call(cmd,cwd=client,env=env))
