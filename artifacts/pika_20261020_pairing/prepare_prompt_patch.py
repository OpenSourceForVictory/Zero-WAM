"""Prepare and validate only task annotations; do not modify source data."""
from pathlib import Path
import json,hashlib,shutil
import pyarrow as pa
import pyarrow.parquet as pq
here=Path(__file__).resolve().parent
root=Path('/media/huawei/Samsung_T5/data/ICL/261020')
dataset=root/'pika_aloha_10_20/lerobot28_v21_h264_crf18_20261020_182114'
rows=json.loads((here/'robot_human_pairs.json').read_text());assert all(r['mapping_status']=='confirmed_recording_order' for r in rows)
patch=here/'lerobot_prompt_patch';patch.mkdir(exist_ok=True)
changes=[]
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def register(rel):changes.append({'path':rel,'before_sha256':sha(dataset/rel) if (dataset/rel).exists() else None,'after_sha256':sha(patch/rel)})
def write(rel,data):
 p=patch/rel;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(data);register(rel)
tasks=[]
for r in rows:
 if r['prompt'] not in tasks:tasks.append(r['prompt'])
 assert len(tasks)<=32
 r['task_index']=tasks.index(r['prompt'])
 rel=f'data/chunk-000/episode_{r["episode_index"]:06d}.parquet';t=pq.read_table(dataset/rel);idx=t.schema.get_field_index('task_index');assert idx>=0
 new=t.set_column(idx,t.schema.field(idx),pa.array([r['task_index']]*t.num_rows,type=t.schema.field(idx).type));p=patch/rel;p.parent.mkdir(parents=True,exist_ok=True);pq.write_table(new,p)
 check=pq.read_table(p);assert check.drop(['task_index']).equals(t.drop(['task_index']));assert set(check['task_index'].to_pylist())=={r['task_index']};register(rel)
write('meta/tasks.jsonl',''.join(json.dumps({'task_index':i,'task':p},ensure_ascii=False)+'\n' for i,p in enumerate(tasks)))
eps=[json.loads(l) for l in (dataset/'meta/episodes.jsonl').read_text().splitlines()];by={r['episode_index']:r for r in rows}
for ep in eps:ep['tasks']=[by[ep['episode_index']]['prompt']]
write('meta/episodes.jsonl',''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in eps))
info=json.loads((dataset/'meta/info.json').read_text());info['total_tasks']=len(tasks);write('meta/info.json',json.dumps(info,ensure_ascii=False,indent=2))
write('meta/human_demo_pairs.json',json.dumps({'path_base':'261020 root (two parents above dataset directory)','mapping_basis':'User confirmed workbook row order for robot episodes and accepted human recordings; discarded videos excluded.','pairs':rows},ensure_ascii=False,indent=2))
write('meta/prompt_annotation.json',json.dumps({'source_workbook':'Zero-WAM_ALOHA_长程任务_含12段验证视频_英文Prompt.xlsx','prompt_count':len(tasks),'robot_episodes':len(rows),'modified':['parquet task_index','meta/tasks.jsonl','meta/episodes.jsonl','meta/info.json'],'preserved':['all non-task parquet columns','videos','HDF5','raw'],'content_visually_verified':False,'mapping_confirmed_by_user':True,'migration_manifest_note':'Original migration checksum manifest predates these annotation changes.'},ensure_ascii=False,indent=2))
(here/'patch_plan.json').write_text(json.dumps({'dataset':str(dataset),'patch':str(patch),'files':changes,'episodes':len(rows),'frames':sum(r['frames'] for r in rows),'tasks':len(tasks),'non_task_columns_unchanged':True},indent=2))
shutil.copy2(here/'prompt与人类示教配对_待确认.xlsx',here/'prompt与人类示教配对.xlsx')
(here/'episode_prompts.json').write_text(json.dumps({str(r['episode_index']):r['prompt'] for r in rows},ensure_ascii=False,indent=2))
print('Validated patch:',len(changes),'files;',len(rows),'episodes;',len(tasks),'prompts; all non-task columns unchanged')
