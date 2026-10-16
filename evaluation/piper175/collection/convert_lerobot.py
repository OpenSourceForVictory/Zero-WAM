"""174-compatible LeRobot conversion: v2.1 by default, 28D actions, RGB, 30 Hz."""
import json
from pathlib import Path
import sys
import h5py

ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT/'reference_174'))
import convert_to_lerobot_v30 as reference


def main():
    import argparse
    selector=argparse.ArgumentParser(add_help=False)
    selector.add_argument('--version',choices=('v2.1','v3.0'),default='v2.1')
    version,remaining=selector.parse_known_args()
    sys.argv=[sys.argv[0],*remaining]
    if version.version=='v2.1':
        import convert_v21_20261016 as v21
        args=v21.parse_args()
        args.image_color_order='rgb'
        if args.remove_video:raise ValueError('Source HDF5 must remain unchanged')
        args.task=args.prompt or args.task
    else:args=reference.parse_args()
    if not any(x=='--action-layout' or x.startswith('--action-layout=') for x in sys.argv[1:]):args.action_layout='legacy28'
    if not any(x=='--fps' or x.startswith('--fps=') for x in sys.argv[1:]):args.fps=30
    source=Path(args.input_dir).resolve()
    for path in source.rglob('*.hdf5'):
        with h5py.File(path) as f:
            if float(f.attrs.get('fps',args.fps))!=args.fps:raise ValueError('FPS differs from capture; no implicit resampling')
            if str(f.attrs.get('color_order','RGB')).lower()!=args.image_color_order:raise ValueError('Image color order differs from input metadata')
    output=Path(args.output_dir)
    if version.version=='v2.1':
        files=v21.find_hdf5_files(source)
        if not files:raise ValueError('No source HDF5 episodes')
        # Use the reference CLI implementation unchanged, including video/statistics layout.
        v21.parse_args=lambda:args
        v21.main()
        episodes=[]
        for index,path in enumerate(files):
            with h5py.File(path) as f:
                episodes.append({'episode_index':index,'source':str(Path(path).relative_to(source)),
                                 'length':len(f['action'])})
        (output/'meta/source_episodes.json').write_text(json.dumps({'action_layout':args.action_layout,'episodes':episodes},indent=2))
    else:reference.convert(args)
    provenance=json.loads((output/'meta/source_episodes.json').read_text())
    with (output/'meta/control_modes.jsonl').open('x') as dest:
        for episode in provenance['episodes']:
            with h5py.File(source/episode['source']) as f:
                if 'control_mode' not in f:continue
                for frame,value in enumerate(f['control_mode'].asstr()):
                    dest.write(json.dumps({'episode_index':episode['episode_index'],'frame_index':frame,
                                           'control_mode':json.loads(value)})+'\n')
    print('Switch labels preserved in meta/control_modes.jsonl; HDF5 originals unchanged.')


if __name__=='__main__':main()
