"""Build a validation checklist and exact-frame previews from exported metadata."""
import csv
import html
import json
from pathlib import Path
import cv2

out = Path(__file__).resolve().parent
root = '/mnt/sfs_turbo/lijianwen/Datasets/piperpika_10_16_zerowam_joint_v2'
local = Path('/media/huawei/Samsung_T5/data/ICL/261016/pika_aloha_10_16_2/lerobot28_v21_h264_crf18_20261017_160250')
rows = [json.loads(x) for x in (out/'episodes.jsonl').read_text().splitlines()]
files = set((out/'data_files.txt').read_text().splitlines())
assert {f"episode_{r['episode_index']:06d}.parquet" for r in rows} == files
catalog = []
sections = []
(out/'images').mkdir(exist_ok=True)
for r in rows:
    ep, group = r['episode_index'], r['alignment_group']
    stem = f'episode_{ep:06d}'
    video_rel = f'videos/chunk-000/observation.images.cam_high/{stem}.mp4'
    c = cv2.VideoCapture(str(local/video_rel))
    assert c.isOpened(), video_rel
    count = int(c.get(cv2.CAP_PROP_FRAME_COUNT)); fps = c.get(cv2.CAP_PROP_FPS)
    assert count == r['length'], (ep,count,r['length'])
    ok, frame = c.read(); assert ok
    cv2.imwrite(str(out/'images'/f'{stem}_initial.png'), frame)
    c.set(cv2.CAP_PROP_POS_FRAMES, count-1); ok, frame = c.read(); assert ok
    cv2.imwrite(str(out/'images'/f'{stem}_final.png'), frame)
    c.release()
    item = dict(group=group, episode=ep, frames=count, reference_duration_s=round(count/fps,2),
        parquet=f'{root}/eval/piperpika/data/chunk-000/{stem}.parquet',
        robot_video=f'{root}/eval/piperpika/{video_rel}',
        human_video=r['icl']['source_human_video_path'],
        human_latent=f'{root}/human_latents/run_eval/group_{group:03d}.pth',
        prompt='pick object to the container', initial_image=f'images/{stem}_initial.png',
        final_image=f'images/{stem}_final.png', status='not_tested', run_log='', outcome='', notes='')
    catalog.append(item)
    sections.append(f'<section><h2>group_{group:03d} / {stem}</h2><p>参考轨迹 {count} 帧，{count/fps:.2f} 秒。左：第0帧；右：末帧。每条轨迹分别核对布局。</p><img src="{item["initial_image"]}" width="640"><img src="{item["final_image"]}" width="640"><p>Human latent: <code>{html.escape(item["human_latent"])}</code></p></section>')
(out/'catalog.json').write_text(json.dumps(catalog, ensure_ascii=False, indent=2))
# Do not overwrite an existing checklist containing manually recorded outcomes.
checklist = out/'test_checklist.csv'
if not checklist.exists():
    with checklist.open('w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=list(catalog[0])); w.writeheader(); w.writerows(catalog)
(out/'index.html').write_text('<!doctype html><meta charset="utf-8"><title>PiperPika 验证集布局与测试清单</title><h1>6组示教 / 12条机器人验证轨迹</h1><p>清单按 eval/piperpika/data 的实际文件校验。图片从本地同版本高质量原视频直接取帧，无裁剪、旋转或标注。机器人首帧用于摆放参考；同组两次轨迹不能假定布局完全相同。人类示教与机器人的位置也可能存在差异。启动前需核对所选轨迹。</p>'+''.join(sections))
print('Verified',len(catalog),'episodes;',len({x['group'] for x in catalog}),'groups;',sum(x['frames'] for x in catalog),'frames')
