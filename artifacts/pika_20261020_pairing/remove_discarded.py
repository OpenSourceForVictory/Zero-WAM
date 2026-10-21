from pathlib import Path
import json,shutil,time
here=Path(__file__).resolve().parent;root=Path('/media/huawei/Samsung_T5/data/ICL/261020');base=root/'pika_aloha_10_20_human/human_video';rows=json.loads((here/'excluded_human_videos.json').read_text());assert len(rows)==4
records=[]
for r in rows:
 d=base/r['directory'];assert d.parent==base and d.is_dir() and not d.is_symlink();review=json.loads((d/'review.json').read_text());assert review.get('accepted') is False and review.get('reason')=='discarded'
 records.append({'path':str(d),'review':review,'files':[{'relative':str(f.relative_to(d)),'bytes':f.stat().st_size} for f in d.rglob('*') if f.is_file()]})
report={'authorized_by_user':True,'started_at':time.time(),'deleted':[],'entries':records};log=here/'discarded_deletion.json';log.write_text(json.dumps(report,ensure_ascii=False,indent=2))
for r in records:
 shutil.rmtree(r['path']);report['deleted'].append(r['path']);log.write_text(json.dumps(report,ensure_ascii=False,indent=2))
assert len(list(base.glob('*/video.mp4')))==32;assert len(list((root/'pika_aloha_10_20_human_verify/human_video').glob('*/video.mp4')))==12
report['complete']=True;log.write_text(json.dumps(report,ensure_ascii=False,indent=2));dest=root/'prompt与示教配对_20261020';dest.mkdir(exist_ok=True);shutil.copy2(log,dest/log.name);print('Removed 4 explicitly discarded episodes; kept 32 training and 12 validation human videos.')
