import json,cv2,numpy as np
from pathlib import Path
here=Path(__file__).resolve().parent;root=Path('/media/huawei/Samsung_T5/data/ICL/261020');remote='/mnt/sfs_turbo/lijianwen/Codes/Zero-WAM/outputs/zeroshot_long_oct21'
rows=json.loads((here.parent/'pika_20261020_pairing/validation_human_index.json').read_text())
for r in rows:
 r['mapping_status']='confirmed_by_user_V01_to_V12_order';r['server_video']='/mnt/sfs_turbo/lijianwen/Datasets/piperpika_10_20/'+r['human_video'];r['latent_path']=remote+'/'+r['video_id']+'.pth'
 cap=cv2.VideoCapture(str(root/r['human_video']));count=int(cap.get(cv2.CAP_PROP_FRAME_COUNT));fps=cap.get(cv2.CAP_PROP_FPS);n=min(129,max(5,int(count/fps*12)));n=(n-1)//4*4+1;ids=np.linspace(0,count-1,n).astype(int);frames=[];cursor=0
 for idx in ids:
  while cursor<=idx:
   ok,bgr=cap.read();assert ok;cursor+=1
  frames.append(cv2.cvtColor(bgr,cv2.COLOR_BGR2RGB))
 cap.release();np.savez_compressed(here/(r['video_id']+'_frames.npz'),frames=np.stack(frames),frame_ids=ids)
 r['sampled_frames']=n;r['video_frames']=count;r['fps']=fps
(here/'validation_manifest.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2));print('Prepared',len(rows),'videos')
