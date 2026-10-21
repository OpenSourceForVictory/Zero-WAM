"""Make an explicit landscape variant of held-out bread demos; retain originals.

This is a selectable preprocessing variant, not an assertion about training.
Full duration and frame count are preserved. No temporal crop or speed-up.
"""
import argparse
import json
from pathlib import Path
import shutil
import subprocess


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--rotation',choices=['ccw90','none'],required=True)
    args=parser.parse_args()
    if __package__:
        from .bread_eval import validate_bundle,digest,save
    else:
        from bread_eval import validate_bundle,digest,save
    import cv2
    from PIL import Image,ImageDraw,ImageOps
    manifest,tests=validate_bundle(args.bundle)
    args.output.mkdir(parents=True,exist_ok=False);(args.output/'videos').mkdir();(args.output/'previews').mkdir()
    for demo in tests:
        source=args.bundle/demo['bundle_path'];dest=args.output/demo['bundle_path']
        vf='transpose=2' if args.rotation=='ccw90' else 'null'
        command=['ffmpeg','-nostdin','-v','error','-n','-i',str(source),'-map','0:v:0',
                 '-vf',vf,'-an','-c:v','libx264','-preset','fast','-crf','18','-pix_fmt','yuv420p',
                 '-metadata:s:v:0','rotate=0','-movflags','+faststart',str(dest)]
        subprocess.run(command,check=True)
        cap=cv2.VideoCapture(str(dest));frames=0;shape=None
        while True:
            ok,frame=cap.read()
            if not ok:break
            shape=list(frame.shape);frames+=1
        fps=cap.get(cv2.CAP_PROP_FPS);cap.release()
        if frames!=demo['frames'] or abs(fps-demo['fps'])>1e-6:
            raise ValueError('Transcode changed frame count or timing')
        # Visual alignment check against the intended pixel rotation, allowing H.264 loss.
        a=cv2.VideoCapture(str(source));ok,original=a.read();a.release();assert ok
        b=cv2.VideoCapture(str(dest));ok,actual=b.read();b.release();assert ok
        import numpy as np
        expected=cv2.rotate(original,cv2.ROTATE_90_COUNTERCLOCKWISE) if args.rotation=='ccw90' else original
        mae=float(np.abs(expected.astype(float)-actual.astype(float)).mean())
        if expected.shape!=actual.shape or mae>8:raise ValueError('Unexpected rotation/transcode result')
        demo.update(source_sha256=demo['sha256'],sha256=digest(dest),bytes=dest.stat().st_size,shape=shape,
                    preprocessing={'rotation':args.rotation,'temporal_crop':False,'speed_change':False,
                                   'first_frame_rotation_mae_255':mae,'command':command})
        preview=args.output/'previews'/f"{demo['id']}.preview.mp4"
        subprocess.run(['ffmpeg','-nostdin','-v','error','-n','-i',str(dest),'-vf','scale=640:-2,fps=12',
                        '-an','-c:v','libx264','-preset','fast','-crf','23',str(preview)],check=True)
        canvas=Image.new('RGB',(640*3,390*2),'white');draw=ImageDraw.Draw(canvas)
        cap=cv2.VideoCapture(str(dest))
        for i,fraction in enumerate([0,.2,.4,.6,.8,1]):
            index=round((frames-1)*fraction);cap.set(cv2.CAP_PROP_POS_FRAMES,index);ok,frame=cap.read();assert ok
            image=ImageOps.contain(Image.fromarray(cv2.cvtColor(frame,cv2.COLOR_BGR2RGB)),(640,360))
            x=i%3*640;y=i//3*390;draw.text((x+4,y+5),f'{index/fps:.2f}s / frame {index}',fill='black');canvas.paste(image,(x,y+26))
        cap.release();canvas.save(args.output/'previews'/f"{demo['id']}.timeline.jpg",quality=90)
    manifest['variant']=args.rotation
    manifest['training_preprocessing_match_confirmed']=False
    save(args.output/'manifest.json',manifest)
    save(args.output/'registry.json',{d['id']:{'video':d['bundle_path']} for d in tests})
    for name in ['training_contract.template.json','trial_record.template.json']:
        shutil.copy2(args.bundle/name,args.output/name)
    validate_bundle(args.output)
    print(json.dumps(tests,indent=2))


if __name__=='__main__':main()
