"""Record only the overhead camera through its shared HTTP service; no ROS/CAN."""
import argparse
import io
import json
from pathlib import Path
import signal
import threading
import time
from urllib.request import urlopen

import cv2
import numpy as np


def read_frame(camera_url, after=0):
    with urlopen(camera_url+'/camera/high.npz?after='+str(after), timeout=2) as response:
        with np.load(io.BytesIO(response.read()), allow_pickle=False) as data:
            frame=data['high'];stamp=float(data['timestamp'])
    if frame.dtype!=np.uint8 or frame.ndim!=3 or frame.shape[2]!=3:
        raise RuntimeError('顶部相机图像格式错误')
    if not np.isfinite(stamp) or stamp<=after or not -.02<=time.time()-stamp<.3:
        raise RuntimeError('顶部相机帧重复或过期')
    return stamp,frame


def record(output, fps, max_frames, camera_url, stop):
    output=Path(output);output.mkdir(parents=True,exist_ok=False)
    manifest={'mode':'human_video','status':'recording','started_at':time.time(),
              'fps':fps,'camera':'high','serial':'CC1C8100041','encoding':'MP4/mp4v',
              'timing':'constant frame rate; source timestamps retained in timestamps.jsonl'}
    def write_meta(): (output/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2))
    write_meta();writer=None;stamps=[];cursor=0.;error=None
    try:
        with (output/'timestamps.jsonl').open('w') as log:
            while not stop.is_set() and len(stamps)<max_frames:
                stamp,frame=read_frame(camera_url,cursor);cursor=stamp
                # Sampling tolerance avoids dropping a 30 Hz frame due to USB jitter.
                if stamps and stamp-stamps[-1]<.75/fps:continue
                if writer is None:
                    height,width=frame.shape[:2];shape=frame.shape
                    writer=cv2.VideoWriter(str(output/'video.partial.mp4'),cv2.VideoWriter_fourcc(*'mp4v'),fps,(width,height))
                    if not writer.isOpened():raise RuntimeError('无法创建 MP4 编码器')
                    manifest.update(width=width,height=height);write_meta()
                    print('Recording '+str(output),flush=True)
                if frame.shape!=shape:raise RuntimeError('相机分辨率发生变化')
                writer.write(frame);stamps.append(stamp)
                log.write(json.dumps({'frame':len(stamps)-1,'source_time':stamp})+'\n')
                if len(stamps)%10==0:print(f'{len(stamps)} frames, overhead video',flush=True)
    except Exception as exc:error=str(exc)
    finally:
        if writer is not None:writer.release()
    # Decode every frame before declaring the take saved/usable.
    decoded=0
    if writer is not None:
        cap=cv2.VideoCapture(str(output/'video.partial.mp4'))
        while True:
            ok,_=cap.read()
            if not ok:break
            decoded+=1
        cap.release()
    duration=stamps[-1]-stamps[0] if len(stamps)>1 else 0
    rate=(len(stamps)-1)/duration if duration else 0
    reasons=[]
    if error:reasons.append(error)
    if len(stamps)<2:reasons.append('视频不足2帧')
    if decoded!=len(stamps):reasons.append('MP4 解码帧数不一致')
    if rate<fps*.8:reasons.append(f'实际视频帧率 {rate:.2f} Hz 低于目标的80%')
    gap=max(np.diff(stamps),default=0)
    if gap>max(.2,3/fps):reasons.append('顶部相机帧间隔过大')
    quality={'frames':len(stamps),'decoded_frames':decoded,'duration_s':duration,'achieved_camera_hz':rate,
             'max_camera_gap_s':float(gap),'valid':not reasons,'failure_reasons':reasons}
    (output/'quality.json').write_text(json.dumps(quality,ensure_ascii=False,indent=2))
    manifest.update(status='failed' if reasons else 'complete',finished_at=time.time(),error=error);write_meta()
    if not reasons:(output/'video.partial.mp4').rename(output/'video.mp4')
    print(f'{len(stamps)} frames, '+json.dumps(quality,ensure_ascii=False),flush=True)
    return 1 if reasons else 0


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',required=True);parser.add_argument('--fps',type=int,default=30)
    parser.add_argument('--max-frames',type=int,default=5000);parser.add_argument('--camera-url',default='http://127.0.0.1:8080')
    args=parser.parse_args()
    if not 1<=args.fps<=30 or not 1<=args.max_frames<=1000000:parser.error('invalid fps/max-frames')
    stop=threading.Event()
    signal.signal(signal.SIGINT,lambda *_:stop.set());signal.signal(signal.SIGTERM,lambda *_:stop.set())
    return record(args.output,args.fps,args.max_frames,args.camera_url,stop)


if __name__=='__main__':raise SystemExit(main())
