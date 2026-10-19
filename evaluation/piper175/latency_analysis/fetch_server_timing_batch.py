"""Fetch completed timing runs via SSH. SSH credentials supplied by caller/environment."""
import json, os, shlex, subprocess
from pathlib import Path
script = r"""
import json
from pathlib import Path
root=Path('/home/piperpika/piper_runtime/inference_client/inference_logs')
runs=[]
for i in range(1,4):
 p=sorted((root/f'oct19_server_timing_batch{i}').glob('*_execute'))[-1]
 def jl(name):return [json.loads(l) for l in (p/name).read_text().splitlines()]
 result=json.loads((p/'result.json').read_text())
 assert result['completed'] and not result['error'],result
 runs.append(dict(run=p.name,path=str(p),result=result,rpc=jl('rpc_diagnostics.jsonl'),chunks=jl('chunk_timings.jsonl'),contract=jl('action_contract.jsonl'),recording=json.loads((p/'continuous/result.json').read_text()),console=Path(f'/tmp/oct19_server_timing_batch{i}.log').read_text()))
print(json.dumps(runs))
"""
cmd=['sshpass','-e','ssh','-o','ConnectTimeout=5','-o','PreferredAuthentications=password','-o','PubkeyAuthentication=no','piperpika@192.168.137.54','python3 -c '+shlex.quote(script)]
raw=subprocess.check_output(cmd)
runs=json.loads(raw)
for r in runs:
 assert len(r['chunks'])==5
 for kind in ('predict','kv'):
  assert sum(x['kind']==kind for x in r['rpc'])==5
 print(r['run'], 'completed',len(r['chunks']), 'stall warnings',r['console'].count('卡住超时'))
Path(__file__).with_name('server_timing_batch_raw.json').write_text(json.dumps(runs,indent=2))
