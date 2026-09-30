"""Download the pinned public sample and verify its recorded SHA256 digests."""
import hashlib
import json
from pathlib import Path
import subprocess
root=Path(__file__).resolve().parents[1]
manifest=json.loads((root/'data/input-manifest.json').read_text())
base=f"https://huggingface.co/datasets/{manifest['repository']}/resolve/{manifest['revision']}/"
for item in manifest['files']:
 local=root/'data'/item['path']
 if item['path'].endswith('.pth'):
  remote='human_latents/robotwin/run_robotwin_20260728_013720_robotwin_n1000/samples/086_robotwin_Use_one_arm_to_grab_the_object_and_put_it_on_the_scale/generated_video_kling-v3.pth'
 else:
  cam=local.stem
  remote=f'robotwin_data/place_object_scale-demo_clean_collect_200-1000/videos/chunk-000/{cam}/episode_000071.mp4'
 if not local.exists():
  local.parent.mkdir(parents=True,exist_ok=True)
  part=local.with_suffix(local.suffix+'.part')
  subprocess.run(['curl','-fL','--retry','3','-o',str(part),base+remote],check=True)
  part.rename(local)
 assert hashlib.sha256(local.read_bytes()).hexdigest()==item['sha256'],f'Hash mismatch: {local}'
 print('Verified',local)
