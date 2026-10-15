"""Export checked dual episodes to the HDF5 schema consumed by Cobot 174.

Images are embedded RGB; vectors are left7,right7. No FK-derived action replacement.
"""
import argparse
import json
import os
from pathlib import Path
import h5py
import numpy as np
from core import build_index

CAMERAS={'high':'cam_high','left':'cam_left_wrist','right':'cam_right_wrist'}


def aloha_vector(row,kind):
    return row['streams'][kind+'_left']['message']['position']+row['streams'][kind+'_right']['message']['position']


def export(episode,destination):
    episode=Path(episode);destination=Path(destination)
    meta=json.loads((episode/'manifest.json').read_text())
    if meta['profile'].get('active_arms',['left','right'])!=['left','right']:
        raise ValueError('Dual-arm data required; no zero padding or fake arm state')
    aligned,quality=build_index(episode)
    if not quality['valid']:raise ValueError('Episode quality check failed')
    if destination.suffix!='.hdf5':raise ValueError('Destination must end in .hdf5')
    destination.parent.mkdir(parents=True,exist_ok=True)
    tmp=destination.with_name(destination.name+'.tmp')
    # Exclusive temp and final creation: no replacement of another recording.
    with h5py.File(tmp,'x') as f:
        f.attrs.update(sim=False,compress=False,color_order='RGB',fps=meta['profile']['hz'],
                       task=meta['task'],vector_order='left6,left_gripper,right6,right_gripper; rad,m',
                       action_semantics=meta['action_semantics'],source_episode=str(episode.resolve()))
        f.create_dataset('observations/qpos',data=np.asarray([aloha_vector(r,'state') for r in aligned],dtype=np.float32))
        f.create_dataset('action',data=np.asarray([aloha_vector(r,'action') for r in aligned],dtype=np.float32))
        f.create_dataset('timestamp',data=[r['time'] for r in aligned])
        if 'control_mode' in aligned[0]['streams']:
            f.create_dataset('control_mode',data=[r['streams']['control_mode']['message']['data'] for r in aligned],dtype=h5py.string_dtype())
        datasets={role:f.create_dataset('observations/images/'+name,(len(aligned),480,640,3),dtype='uint8',chunks=(1,480,640,3)) for role,name in CAMERAS.items()}
        for i,row in enumerate(aligned):
            path=(episode/row['frame']).resolve()
            if not path.is_relative_to(episode.resolve()):raise ValueError('Image path escapes episode')
            with np.load(path,allow_pickle=False) as images:
                for role,ds in datasets.items():
                    image=images[role]
                    if image.shape!=(480,640,3) or image.dtype!=np.uint8:raise ValueError('Invalid image')
                    ds[i]=image[...,::-1] # source BGR -> explicit RGB
        f.attrs['manifest']=(episode/'manifest.json').read_text()
    # Same-filesystem hardlink provides atomic, exclusive destination publication.
    os.link(tmp,destination);tmp.unlink()
    return str(destination)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('episode');p.add_argument('destination')
    a=p.parse_args();print(export(a.episode,a.destination))
