"""Copy inventoried validation media without modifying source data; resumable rsync."""
import concurrent.futures,json,os,subprocess,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]/'outputs/validation_review_20261019'
runs=json.loads((ROOT/'source_inventory.json').read_text())

def copy_robot():
 env=dict(os.environ)
 if not env.get('SSHPASS'):raise RuntimeError('Set SSHPASS for robot SSH')
 for r in sorted(runs,key=lambda r:(r['group'],r['run'])):
  dest=ROOT/('group_'+r['group'])/r['run']/'robot';dest.mkdir(parents=True,exist_ok=True)
  cmd=['rsync','-a','--partial','--timeout=120','-e','sshpass -e ssh -o ConnectTimeout=8 -o PreferredAuthentications=password -o PubkeyAuthentication=no',f"piperpika@192.168.137.54:{r['source']}/",str(dest)+'/']
  subprocess.run(cmd,env=env,check=True)
  print('ROBOT_DONE',r['group'],r['run'],flush=True)

def copy_server():
 for r in sorted(runs,key=lambda r:(r['group'],r['run'])):
  if not r['trace']:continue
  dest=ROOT/('group_'+r['group'])/r['run']/'server_prediction';dest.mkdir(parents=True,exist_ok=True)
  cmd=['rsync','-a','--partial','--timeout=120','--include=*.mp4','--include=*.json','--include=*.jsonl','--exclude=*','-e','ssh -o BatchMode=yes -o ConnectTimeout=8',f"root@1.95.69.29:{r['trace']}/",str(dest)+'/']
  subprocess.run(cmd,check=True)
  print('SERVER_DONE',r['group'],r['run'],flush=True)

with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
 futures=[pool.submit(copy_robot),pool.submit(copy_server)]
 for f in futures:f.result()
print('COPY_COMPLETE',flush=True)
