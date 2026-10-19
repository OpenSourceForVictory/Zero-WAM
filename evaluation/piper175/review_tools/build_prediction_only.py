"""Create a prediction-only review directory from downloaded server videos."""
import concurrent.futures,csv,hashlib,html,json,shutil,subprocess
from pathlib import Path
BASE=Path(__file__).resolve().parents[3]
SRC=BASE/'outputs/validation_review_20261019'
OUT=BASE/'outputs/validation_predictions_20261019';OUT.mkdir(exist_ok=True)
rs=sorted(json.loads((SRC/'source_inventory.json').read_text()),key=lambda r:(r['group'],r['run']))
servers={r['trace']:r for r in json.loads((SRC/'server_inventory.json').read_text())}
style='<meta charset="utf-8"><style>body{font:16px system-ui;max-width:1200px;margin:32px auto;padding:16px;line-height:1.6}table{border-collapse:collapse}td,th{border:1px solid #ccc;padding:10px}video{width:700px;max-width:100%}summary{cursor:pointer}a{color:#1765a5}</style>'
def esc(x):return html.escape(str(x))
rows=[];top=[style,'<h1>验证集服务端预测视频 · 2026-10-19</h1><p>仅包含服务端预测视频，非真机实拍。六组共 19 次运行。模型预测不等于动作已执行，更不等于任务成功。各运行包含不同输入分辨率和采样用途。</p>']
for group in sorted({r['group'] for r in rs}):
 top.append('<h2>group_'+group+'</h2><table><tr><th>运行批次</th><th>用途</th><th>预测视频数量</th></tr>')
 for r in [x for x in rs if x['group']==group]:
  relative=Path('group_'+group)/r['run'];dest=OUT/relative;dest.mkdir(parents=True,exist_ok=True)
  videos=[x for x in servers[r['trace']]['files'] if x['path'].endswith('.mp4')]
  acknowledged={Path(a['video_path']).name for a in r['artifacts']}
  purpose='时延采样' if 'server_timing' in r['source'] else '验证运行'
  top.append('<tr><td><a href="'+relative.as_posix()+'/index.html">'+r['run']+'</a></td><td>'+purpose+'</td><td>'+str(len(videos))+'</td></tr>')
  body=[style,'<p><a href="../../index.html">返回总览</a></p><h1>group_'+group+' / '+r['run']+'</h1><p>按 chunk 编号顺序浏览。未收到客户端响应记录的预测可能在停止后完成，不能当成已执行动作。</p>']
  for f in videos:
   original=SRC/relative/'server_prediction'/f['path'];target=dest/f['path']
   assert original.stat().st_size==f['bytes']
   shutil.copy2(original,target)
   ack=f['path'] in acknowledged
   body.append('<details><summary>'+f['path']+('' if ack else ' — 无客户端响应记录，执行情况未确认')+'</summary><video controls preload="none" src="'+f['path']+'"></video><p><a href="'+f['path']+'">打开视频</a></p></details>')
   rows.append(dict(group=group,run=r['run'],file=str(relative/f['path']),bytes=f['bytes'],client_response_recorded=ack,source=r['trace']+'/'+f['path'],sha256=hashlib.sha256(target.read_bytes()).hexdigest()))
  (dest/'index.html').write_text('\n'.join(body))
 top.append('</table>')
(OUT/'index.html').write_text('\n'.join(top))
with (OUT/'videos.csv').open('w',newline='') as f:
 w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)

def probe(row):
 p=OUT/row['file']
 result=subprocess.run(['ffprobe','-v','error','-select_streams','v:0','-show_entries','stream=codec_name,width,height','-show_entries','format=duration','-of','json',str(p)],capture_output=True,text=True)
 data=json.loads(result.stdout) if result.returncode==0 else {}
 ok=result.returncode==0 and bool(data.get('streams')) and float(data.get('format',{}).get('duration',0))>0
 return dict(file=row['file'],ok=ok,probe=data,error=result.stderr)
with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:checks=list(pool.map(probe,rows))
(OUT/'verification.json').write_text(json.dumps(dict(files=len(rows),bytes=sum(r['bytes'] for r in rows),source_sizes_match=True,ffprobe_pass=sum(x['ok'] for x in checks),checks=checks),indent=2))
assert all(x['ok'] for x in checks)
(OUT/'README.txt').write_text('打开 index.html 浏览服务端预测视频。\n目录按 group / 运行时间 / chunk_XXXXX.mp4 排列，共 6 组、19 次运行、313 个视频。\n只有预测视频和索引，没有真机实拍录像。\n没有重新编码；源文件未修改或删除。videos.csv 记录源路径、字节数、本地 SHA256 与客户端响应记录是否存在。\n校验：313 个视频大小与源目录清单一致，ffprobe 可识别视频流及有效时长。此项不等同于逐帧完整解码。\n4 个视频没有对应的客户端响应记录，可能在停止之后完成，不代表动作已执行。\n')
print(json.dumps(dict(directory=str(OUT),videos=len(rows),runs=len(rs),bytes=sum(r['bytes'] for r in rows),ffprobe_pass=sum(x['ok'] for x in checks)),ensure_ascii=False))
