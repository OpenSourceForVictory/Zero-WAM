"""Verify every published model file, with official LFS SHA256 hashes."""
import hashlib
import json
from pathlib import Path
root=Path(__file__).resolve().parents[1]
target=root/'models/zero-wam-posttrain-robotwin'
entries=json.loads((root/'models/manifest.json').read_text())
report=[]
for item in entries:
 if item['type']!='file': continue
 path=target/item['path']
 assert path.is_file(), f'Missing {path}'
 assert path.stat().st_size==item['size'], f'Size mismatch: {path}'
 result=dict(path=item['path'],size=item['size'])
 if 'lfs' in item:
  h=hashlib.sha256()
  with path.open('rb') as f:
   while chunk:=f.read(8*1024*1024): h.update(chunk)
  assert h.hexdigest()==item['lfs']['oid'],f'SHA256 mismatch: {path}'
  result['sha256']=h.hexdigest()
 report.append(result)
 print('Verified',item['path'],flush=True)
(root/'logs/checkpoint-verification.json').write_text(json.dumps(report,indent=2)+'\n')
