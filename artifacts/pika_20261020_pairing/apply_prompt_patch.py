"""Apply prevalidated annotation patch with backup and rollback."""
from pathlib import Path
import json,hashlib,shutil,time
here=Path(__file__).resolve().parent;p=json.loads((here/'patch_plan.json').read_text());dataset=Path(p['dataset']);patch=Path(p['patch']);root=dataset.parent.parent
sha=lambda f:hashlib.sha256(f.read_bytes()).hexdigest()
for r in p['files']:
 f=dataset/r['path'];assert (sha(f) if f.exists() else None)==r['before_sha256'],str(f);assert sha(patch/r['path'])==r['after_sha256']
backup=dataset.parent/('prompt_annotation_backup_'+time.strftime('%Y%m%d_%H%M%S'));backup.mkdir()
for r in p['files']:
 f=dataset/r['path']
 if f.exists():
  b=backup/r['path'];b.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(f,b)
shutil.copy2(here/'patch_plan.json',backup/'patch_plan.json')
applied=[]
try:
 for r in p['files']:
  f=dataset/r['path'];tmp=f.with_name(f.name+'.prompt_update_tmp');shutil.copy2(patch/r['path'],tmp);tmp.replace(f);applied.append(r)
 for r in p['files']:assert sha(dataset/r['path'])==r['after_sha256']
except BaseException:
 for r in reversed(applied):
  f=dataset/r['path']
  if r['before_sha256'] is None:f.unlink()
  else:shutil.copy2(backup/r['path'],f)
 raise
art=root/'prompt与示教配对_20261020';art.mkdir(exist_ok=True)
names=['prompt与人类示教配对.xlsx','episode_prompts.json','robot_human_pairs.csv','robot_human_pairs.json','human_demo_index.csv','human_demo_index.json','excluded_human_videos.csv','excluded_human_videos.json','validation_human_index.csv','validation_human_index.json','audit.json','build_pairing.py','prepare_prompt_patch.py','apply_prompt_patch.py','patch_plan.json']
for n in names:shutil.copy2(here/n,art/n)
result={'complete':True,'backup':str(backup),'artifact_directory':str(art),'dataset':str(dataset),'episodes':p['episodes'],'frames':p['frames'],'tasks':p['tasks'],'non_task_columns_unchanged':True,'validation_mapping_confirmed':False}
(here/'applied_result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2));(art/'applied_result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2));print(json.dumps(result,ensure_ascii=False,indent=2))
