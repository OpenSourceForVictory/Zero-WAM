"""Compare decoded LeRobot videos with their source HDF5; inputs are read-only."""
import argparse
import json
from pathlib import Path
import av
import h5py
import numpy as np


def compare(source, reference, candidate, episode_index=0):
    report={}
    with h5py.File(source,'r') as f:
        for cam,ds in f['observations/images'].items():
            indices=set(range(0,len(ds),30))|{len(ds)//2,len(ds)-1}
            details={}
            for label,root in [('reference',reference),('candidate',candidate)]:
                path=Path(root)/f'videos/chunk-000/observation.images.{cam}/episode_{episode_index:06d}.mp4'
                values=[];count=0
                with av.open(str(path)) as container:
                    stream=container.streams.video[0]
                    codec=stream.codec_context.name
                    for i,frame in enumerate(container.decode(video=0)):
                        count+=1
                        if i not in indices:continue
                        decoded=frame.to_ndarray(format='rgb24');original=ds[i]
                        if decoded.shape!=original.shape:raise ValueError('Frame dimensions changed')
                        mse=np.mean((decoded.astype(np.float32)-original.astype(np.float32))**2)
                        values.append(float(10*np.log10(255**2/max(mse,1e-10))))
                if count!=len(ds):raise ValueError('Video frame count differs from source')
                details[label]={'codec':codec,'frames':count,'samples':len(values),'mean_psnr_db':float(np.mean(values)),
                                'min_psnr_db':min(values),'bytes':path.stat().st_size}
            report[cam]=details
    return report


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('source');p.add_argument('reference');p.add_argument('candidate');p.add_argument('--episode-index',type=int,default=0)
    p.add_argument('--report',required=True);a=p.parse_args()
    result=compare(a.source,a.reference,a.candidate,a.episode_index)
    with Path(a.report).open('x') as out:json.dump(result,out,indent=2)
    print(json.dumps(result,indent=2))
