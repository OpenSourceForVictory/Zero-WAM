"""Read-only verification of a raw-action LeRobot conversion against source HDF5."""
import argparse
import json
from pathlib import Path
import av
import h5py
import numpy as np
import pyarrow.parquet as pq


def verify(source,converted):
    source=Path(source);converted=Path(converted)
    info=json.loads((converted/'meta/info.json').read_text())
    mapping=json.loads((converted/'meta/source_episodes.json').read_text())
    if mapping['action_layout']!='raw':raise ValueError('This verification expects raw action semantics')
    tables=[pq.read_table(p).to_pydict() for p in sorted((converted/'data').rglob('*.parquet'))]
    rows={}
    for table in tables:
        for i,episode in enumerate(table['episode_index']):
            rows.setdefault(int(episode),[]).append({k:v[i] for k,v in table.items()})
    count=0
    for ep in mapping['episodes']:
        samples=sorted(rows[ep['episode_index']],key=lambda r:r['frame_index'])
        with h5py.File(source/ep['source']) as f:
            assert len(samples)==len(f['action'])==ep['length']
            np.testing.assert_allclose(np.asarray([r['action'] for r in samples]),f['action'][:],atol=1e-7)
            np.testing.assert_allclose(np.asarray([r['observation.state'] for r in samples]),f['observations/qpos'][:],atol=1e-7)
            count+=len(samples)
    assert count==info['total_frames'] and len(mapping['episodes'])==info['total_episodes']
    videos=list((converted/'videos').rglob('*.mp4'))
    video_counts={}
    for video in videos:
        with av.open(str(video)) as container:
            video_counts[str(video.relative_to(converted))]=sum(1 for _ in container.decode(video=0))
    camera_count=sum(k.startswith('observation.images.') for k in info['features'])
    assert sum(video_counts.values())==count*camera_count
    labels=[json.loads(x) for x in (converted/'meta/control_modes.jsonl').read_text().splitlines()]
    expected=[]
    for ep in mapping['episodes']:
        with h5py.File(source/ep['source']) as f:
            if 'control_mode' in f:
                expected.extend({'episode_index':ep['episode_index'],'frame_index':i,'control_mode':json.loads(x)}
                                for i,x in enumerate(f['control_mode'].asstr()))
    assert labels==expected
    report={'valid':True,'episodes':len(rows),'frames':count,'camera_count':camera_count,
            'decoded_video_frames':video_counts,'switch_labels':len(labels),'fps':info['fps'],
            'action_semantics':'raw HDF5 target; left7,right7'}
    print(json.dumps(report,indent=2));return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input_dir');parser.add_argument('output_dir');a=parser.parse_args();verify(a.input_dir,a.output_dir)
