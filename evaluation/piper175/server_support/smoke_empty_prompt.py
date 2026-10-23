"""Reset and predict from a saved observation; never imports or opens robot hardware.

This consumes a model session. Run only when the selected server is idle.
"""
import argparse
import json
from pathlib import Path
import sys
import time

import numpy as np
from PIL import Image
from websockets.sync.client import connect


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--client-dir', required=True)
    p.add_argument('--host', required=True)
    p.add_argument('--port', type=int, required=True)
    p.add_argument('--latent', required=True)
    p.add_argument('--snapshot', required=True)
    p.add_argument('--output', required=True)
    args = p.parse_args()
    sys.path.insert(0, args.client_dir)
    from msgpack_numpy import Packer, unpackb
    packer = Packer()
    snapshot = np.load(args.snapshot)
    obs = {'task': '', 'observation.state': np.zeros(30, dtype=np.float32)}
    obs['observation.state'][:len(snapshot['state'])] = snapshot['state']
    for source, target in [('high', 'cam_high'), ('left', 'cam_left_wrist'), ('right', 'cam_right_wrist')]:
        obs['observation.images.' + target] = np.array(
            Image.fromarray(snapshot[source][:, :, ::-1]).resize((320, 240), Image.Resampling.BOX))
    report = {'prompt': '', 'latent': args.latent, 'snapshot': args.snapshot, 'hardware_connected': False}
    with connect(f'ws://{args.host}:{args.port}', compression=None, max_size=None,
                 ping_interval=None, open_timeout=10) as ws:
        metadata = ws.recv(timeout=30)
        if isinstance(metadata, str):
            raise RuntimeError(metadata)
        for phase, request in [
            ('reset', {'reset': True, 'prompt': '', 'use_icl': True,
                       'icl_latent_path': args.latent, 'video_guidance_scale': -1, 'icl_guidance_scale': 5}),
            ('predict', {'obs': obs, 'prompt': ''}),
        ]:
            start = time.monotonic()
            ws.send(packer.pack(request))
            response = ws.recv(timeout=300)
            if isinstance(response, str):
                raise RuntimeError(response)
            result = unpackb(response)
            report[phase + '_s'] = time.monotonic() - start
            if phase == 'reset':
                assert result.get('text_conditioning') == 'video_only_fixed_empty_v1', result.get('text_conditioning')
                assert result.get('target_text_enabled') is False
                report['reset'] = result
            else:
                action = np.asarray(result['action'])
                assert action.shape == (14, 2, 16) and np.isfinite(action).all(), action.shape
                report['action_shape'] = list(action.shape)
                report['prediction_artifacts'] = result.get('prediction_artifacts')
            print(phase, round(report[phase + '_s'], 3), 'seconds', flush=True)
    Path(args.output).write_text(json.dumps(report, indent=2, default=str))
    print('PASS: empty prompt reset and finite action prediction; no robot hardware used', flush=True)


if __name__ == '__main__':
    main()
