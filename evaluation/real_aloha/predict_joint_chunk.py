"""Request one absolute ALOHA chunk from a live endpoint; never connects to hardware."""
import argparse
import hashlib
import json
from pathlib import Path
import time

import numpy as np


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--observation', type=Path, required=True)
    p.add_argument('--human-latent', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--host', default='127.0.0.1')
    p.add_argument('--port', type=int, default=29056)
    p.add_argument('--seed', type=int, default=42)
    p.add_argument('--prompt', default='Follow the human demonstration to prepare breakfast.')
    args = p.parse_args()
    import websockets.sync.client
    from evaluation.robotwin.msgpack_numpy import Packer, unpackb
    with np.load(args.observation, allow_pickle=False) as data:
        obs = {k: data[k] for k in data.files}
    expected = {'observation.images.cam_' + n for n in ('high', 'left_wrist', 'right_wrist')}
    if set(obs) != expected or any(a.dtype != np.uint8 or a.ndim != 3 or a.shape[-1] != 3 for a in obs.values()):
        raise ValueError('Expected three RGB uint8 camera images')
    args.output.mkdir(parents=True, exist_ok=False)
    record = {'physical_commands': 0, 'seed': args.seed, 'human_latent': str(args.human_latent),
              'latent_sha256': hashlib.sha256(args.human_latent.read_bytes()).hexdigest(),
              'observation_sha256': hashlib.sha256(args.observation.read_bytes()).hexdigest(),
              'started_at': time.time(), 'completed': False}
    def save():
        (args.output / 'report.json').write_text(json.dumps(record, indent=2, default=lambda x: x.tolist()) + '\n')
    save()
    packer = Packer()
    try:
        with websockets.sync.client.connect(f'ws://{args.host}:{args.port}', open_timeout=30,
                max_size=None, compression=None, ping_interval=None) as ws:
            record['metadata'] = unpackb(ws.recv(timeout=30))
            def infer(message):
                ws.send(packer.pack(message))
                response = ws.recv(timeout=1800)
                if isinstance(response, str):
                    raise RuntimeError(response)
                return unpackb(response)
            record['reset'] = infer({'reset': True, 'seed': args.seed, 'prompt': args.prompt,
                'use_icl': True, 'icl_latent_path': str(args.human_latent.resolve()),
                'video_guidance_scale': -1, 'icl_guidance_scale': 5})
            if record['reset'].get('action_contract') != 'aloha_absolute_28_continuous_v2' or not record['reset'].get('checkpoint_contract_bound'):
                raise ValueError('Expected checkpoint-bound absolute ALOHA v2 endpoint')
            save()
            action = np.asarray(infer({'obs': obs})['action'])
            np.save(args.output / 'raw_action.npy', action)
            if action.shape != (28, 2, 16) or not np.isfinite(action).all():
                raise ValueError(f'Expected finite (28,2,16), got {action.shape}')
            steps = action[:, 1, :].T.copy()  # Discard the initial padded latent block.
            np.save(args.output / 'absolute_actions.npy', steps)
            np.save(args.output / 'joint_targets_rad.npy', steps[:, 14:26].reshape(-1, 2, 6))
            record.update(completed=True, action_shape=list(action.shape),
                first_joint_deg=np.rad2deg(steps[0, 14:26]).reshape(2, 6).tolist(),
                first_gripper=steps[0, 26:28].tolist(), finished_at=time.time())
    except Exception as exc:
        record['error'] = str(exc)
        raise
    finally:
        save()
    print(json.dumps(record, indent=2, default=lambda x: x.tolist()))


if __name__ == '__main__':
    main()
