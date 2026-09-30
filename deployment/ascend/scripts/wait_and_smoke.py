"""Wait for complete checkpoint files, verify hashes, then run a one-step probe."""
import json
from pathlib import Path
import subprocess
import time
root=Path(__file__).resolve().parents[1]
manifest=json.loads((root/'models/manifest.json').read_text())
files=[f for f in manifest if f['type']=='file']
target=root/'models/zero-wam-posttrain-robotwin'
while True:
 pending=[f['path'] for f in files if not (target/f['path']).is_file() or (target/f['path']).stat().st_size!=f['size']]
 if not pending: break
 print(time.strftime('%H:%M:%S'),len(pending),'files pending',flush=True)
 time.sleep(30)
subprocess.run(['python3',str(root/'scripts/verify_checkpoint.py')],check=True)
with (root/'logs/inference-smoke.log').open('w') as log:
 subprocess.run([str(root/'run_inference.sh'),'--steps','1','--chunks','1','--output',str(root/'outputs/smoke')],stdout=log,stderr=subprocess.STDOUT,check=True)
print('SMOKE PASSED',flush=True)
