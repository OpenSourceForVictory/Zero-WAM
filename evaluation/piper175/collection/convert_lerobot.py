"""175 wrapper around the pinned 174 LeRobot v3.0 converter.

Defaults: raw 14D commands, RGB, 10 Hz. Preserve switching labels in a sidecar.
"""
import json
from pathlib import Path
import sys
import h5py

ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT/'reference_174'))
import convert_to_lerobot_v30 as reference


def main():
    args=reference.parse_args()
    if not any(x=='--action-layout' or x.startswith('--action-layout=') for x in sys.argv[1:]):args.action_layout='raw'
    if not any(x=='--fps' or x.startswith('--fps=') for x in sys.argv[1:]):args.fps=10
    source=Path(args.input_dir).resolve()
    for path in source.rglob('*.hdf5'):
        with h5py.File(path) as f:
            if float(f.attrs.get('fps',args.fps))!=args.fps:raise ValueError('FPS differs from capture; no implicit resampling')
            if str(f.attrs.get('color_order','RGB')).lower()!=args.image_color_order:raise ValueError('Image color order differs from input metadata')
    reference.convert(args)
    output=Path(args.output_dir)
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
