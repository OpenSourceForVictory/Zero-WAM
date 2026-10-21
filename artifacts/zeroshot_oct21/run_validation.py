"""One explicitly selected validation video per run; never advances scenarios or clears stop."""
import argparse,json,os,subprocess
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('video_id',choices=[f'V{i:02d}' for i in range(1,13)]);p.add_argument('--mode',choices=['check','shadow','execute'],default='check');p.add_argument('--max-chunks',type=int,default=1);p.add_argument('--port',type=int,default=39057);a=p.parse_args();assert a.max_chunks>=0
here=Path(__file__).resolve().parent;row=next(r for r in json.loads((here/'validation_manifest.json').read_text()) if r['video_id']==a.video_id)
client=Path('/home/piperpika/piper_runtime/inference_client');env=os.environ.copy();env.update(ZERO_WAM_ICL_LATENT_PATH=row['latent_path'],ZERO_WAM_VIDEO_GUIDANCE_SCALE='1',ZERO_WAM_ICL_GUIDANCE_SCALE='5');env.pop('ZERO_WAM_ICL_ID',None)
print(json.dumps(row,ensure_ascii=False,indent=2),flush=True)
cmd=['bash',str(client/'run.sh'),'--'+a.mode,'--action-contract','piperpika','--prompt',row['prompt'],'--server_port',str(a.port),'--max_chunks',str(a.max_chunks),'--save_dir',str(client/'inference_logs/zeroshot_oct21'/a.video_id)]
if a.mode!='check':cmd.append('--record-continuous')
raise SystemExit(subprocess.call(cmd,cwd=client,env=env))
