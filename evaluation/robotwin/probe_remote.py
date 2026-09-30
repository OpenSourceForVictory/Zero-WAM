"""Exercise the remote model with recorded images; does not require SAPIEN or torch."""
import argparse
import json
from pathlib import Path
import urllib.request

import imageio.v2 as imageio
import numpy as np

from .websocket_client_policy import WebsocketClientPolicy

KEYS = [f'observation.images.{camera}' for camera in
        ('cam_high', 'cam_left_wrist', 'cam_right_wrist')]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=29056)
    parser.add_argument('--icl-id', default='place_object_scale')
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=Path('remote-probe.json'))
    args = parser.parse_args()
    with urllib.request.urlopen(f'http://{args.host}:{args.port}/healthz', timeout=10) as response:
        assert response.status == 200

    def observation(frame):
        result = {}
        for key in KEYS:
            with imageio.get_reader(str(args.input / f'{key}.mp4'), format='ffmpeg') as reader:
                result[key] = reader.get_data(frame).copy()
        return result

    client = WebsocketClientPolicy(args.host, args.port)
    try:
        metadata = client.get_server_metadata()
        assert args.icl_id in metadata.get('icl_ids', []), metadata
        client.infer(dict(reset=True, use_icl=True, icl_id=args.icl_id,
                          prompt='Use one arm to grab the object and put it on the scale.',
                          video_guidance_scale=-1, icl_guidance_scale=5))
        chunks = []
        for index in range(2):
            if index:
                client.infer(dict(obs=[observation(i) for i in (4, 8, 12, 16)],
                                  compute_kv_cache=True))
            result = client.infer(dict(obs=[observation(0 if index == 0 else 16)]))
            action = result['action']
            assert action.shape == (16, 2, 16) and np.isfinite(action).all()
            chunks.append(dict(shape=list(action.shape), finite=True,
                               server_timing=result['server_timing']))
        report = dict(mode='recorded observations over WebSocket; no simulation',
                      metadata=metadata, chunks=chunks)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + '\n')
        print(json.dumps(report, indent=2))
    finally:
        client.close()


if __name__ == '__main__':
    main()
