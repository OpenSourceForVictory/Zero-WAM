"""Build portable HTML indexes from source inventories; no media conversion."""
import csv,html,json,shutil
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]/'outputs/validation_review_20261019'
runs=sorted(json.loads((ROOT/'source_inventory.json').read_text()),key=lambda r:(r['group'],r['run']))
servers={r['trace']:r for r in json.loads((ROOT/'server_inventory.json').read_text())}
style='<meta charset="utf-8"><style>body{font:16px system-ui;max-width:1400px;margin:32px auto;padding:20px;line-height:1.6}table{border-collapse:collapse;width:100%}td,th{border:1px solid #ccc;padding:8px;text-align:left}video{width:420px;max-width:100%}a{color:#1264a3}summary{cursor:pointer}section{margin:24px 0}code{overflow-wrap:anywhere}</style>'
def esc(s):return html.escape(str(s))
def link(path,label):return '<a href="'+esc(path)+'">'+esc(label)+'</a>'
summary=[];sections=[]
for r in runs:
 base=ROOT/('group_'+r['group'])/r['run'];base.mkdir(parents=True,exist_ok=True)
 srv=servers.get(r['trace'],{'files':[]})
 videos=[f['path'] for f in srv['files'] if f['path'].endswith('.mp4')]
 acknowledged={Path(a['video_path']).name for a in r['artifacts']}
 extra=[v for v in videos if v not in acknowledged]
 missing=sorted(acknowledged-set(videos))
 continuous=[f['path'] for f in r['files'] if f['path'].startswith('continuous/') and f['path'].endswith('.avi')]
 shape=r['manifest'].get('request_image_shapes',r['manifest'].get('camera_shapes',[]))
 size=(str(shape[0][1])+'×'+str(shape[0][0])) if shape else '?'
 reset=r['reset'];vstep=reset.get('video_inference_steps',reset.get('inference_steps','未记录'));astep=reset.get('action_inference_steps',reset.get('inference_steps','未记录'))
 label='时延采样' if 'server_timing' in r['source'] else '验证运行'
 row=dict(group=r['group'],run=r['run'],category=label,duration_s=r['recording'].get('duration_s'),input_size=size,video_steps=vstep,action_steps=astep,continuous_segments=len(continuous),predicted_videos=len(videos),unacknowledged_predictions=len(extra),missing_predictions=len(missing),sent_steps=r['result'].get('sent_steps'),task_success=r['result'].get('task_success'),robot_source=r['source'],server_source=r['trace'])
 summary.append(row)
 page=[style,'<h1>group_'+r['group']+' · '+r['run']+'</h1>',link('../../index.html','返回总览'),'<p>'+esc(label)+'；输入 '+size+'；步数 '+str(vstep)+'/'+str(astep)+'；成功率未判定。</p>', '<h2>实拍连续录像（AVI 原件）</h2><p>high＝顶部全景，left＝左腕，right＝右腕。按编号顺序播放；AVI 建议用 VLC。时间对齐以 continuous/timestamps.csv 为准，不能仅按视频播放进度严格对齐预测。</p>']
 for role in ('high','left','right'):
  page.append('<details><summary>'+role+'</summary><ul>'+''.join('<li>'+link('robot/'+f,Path(f).name)+'</li>' for f in continuous if Path(f).name.startswith(role+'_'))+'</ul></details>')
 page.append('<h2>实拍关键帧视频（不是连续录像）</h2>')
 for name in ('cam_high.mp4','cam_left.mp4','cam_right.mp4','combined.mp4'):
  if any(f['path']==name for f in r['files']):page.append('<p>'+link('robot/'+name,name)+'</p>')
 page.append('<h2>服务端预测视频（按 chunk）</h2><p>模型预测画面，不是真机实拍；预测输出不等于动作已完整执行。标注“未收到响应记录”的视频可能在用户停止后由服务端完成。</p>')
 for name in videos:
  note=' · 未收到响应记录，执行情况未确认' if name in extra else ''
  page.append('<details><summary>'+esc(name+note)+'</summary><video controls preload="none" src="server_prediction/'+name+'"></video></details>')
 page.append('<h2>日志和来源</h2><ul>'+''.join('<li>'+link('robot/'+name,name)+'</li>' for name in ('manifest.json','result.json','rpc_diagnostics.jsonl','chunk_timings.jsonl','prediction_artifacts.jsonl','continuous/timestamps.csv') if any(f['path']==name for f in r['files']))+'</ul>')
 page.append('<p>机器人源目录：<code>'+esc(r['source'])+'</code></p><p>预测源目录：<code>'+esc(r['trace'])+'</code></p>')
 (base/'index.html').write_text('\n'.join(page));(base/'run_summary.json').write_text(json.dumps(row,ensure_ascii=False,indent=2))
 (base/'prediction_mapping.json').write_text(json.dumps(dict(acknowledged=sorted(acknowledged),extra_without_client_response=extra,missing=missing),indent=2))
for group in sorted({r['group'] for r in runs}):
 rs=[r for r in summary if r['group']==group]
 sections.append('<h2>group_'+group+'</h2><table><tr><th>运行时间</th><th>用途</th><th>时长(秒)</th><th>输入 / video-action 步数</th><th>连续视频分段数(三路合计)</th><th>预测视频</th></tr>'+''.join('<tr><td>'+link('group_'+group+'/'+r['run']+'/index.html',r['run'])+'</td><td>'+r['category']+'</td><td>'+str(round(r['duration_s'] or 0,1))+'</td><td>'+r['input_size']+' / '+str(r['video_steps'])+'-'+str(r['action_steps'])+'</td><td>'+str(r['continuous_segments'])+'</td><td>'+str(r['predicted_videos'])+'</td></tr>' for r in rs)+'</table>')
(ROOT/'index.html').write_text(style+'<h1>2026-10-19 验证集真机运行归档</h1><p>6 组，19 次运行。原始连续录像、关键帧视频、服务端预测视频与诊断日志按运行批次配对。包含 group_002 的分辨率/步数对比和时延采样，不能把全部批次视为相同配置。成功率未评定。</p>'+''.join(sections))
with (ROOT/'runs.csv').open('w',newline='') as f:
 w=csv.DictWriter(f,fieldnames=list(summary[0]));w.writeheader();w.writerows(summary)
(ROOT/'README.txt').write_text('打开 index.html 浏览六组及所有运行。\n归档机器：192.168.137.54\n目录：/home/piperpika/piper_runtime/validation_review_20261019\n每次运行 robot/ 是真机原始记录完整副本；robot/continuous/ 是三路连续 AVI；robot/cam_*.mp4 是关键帧视频，不是连续录像。\nserver_prediction/ 是对应服务端预测 MP4 及解码元数据，未复制大型中间 latent .pt；它们仍保留在服务端原目录。\n原始目录不移动、不删除，不重新编码视频。runs.csv 与 source_inventory.json/server_inventory.json 记录来源。\n同组的两条验证集 episode 共用示教；本归档按真实会话匹配，不臆定每次现场布局属于哪一条 episode。\n额外预测视频无客户端响应记录时，已在各次运行页面标注，不代表真机执行。\n')
print('runs',len(summary),'continuous',sum(r['continuous_segments'] for r in summary),'predictions',sum(r['predicted_videos'] for r in summary),'extras',sum(r['unacknowledged_predictions'] for r in summary),'missing',sum(r['missing_predictions'] for r in summary))
