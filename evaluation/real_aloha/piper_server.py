"""Independent Piper inference entry; requires explicitly selected stats and timing.

This does not certify the checkpoint for Piper or operate hardware.
"""
import argparse
import copy
import hashlib
import json
import sys
from pathlib import Path
import numpy as np
from .piper_contract import PiperContract, ActionQuantiles, USED


def configure(base, profile_path):
    path = Path(profile_path).resolve()
    contract = PiperContract.load(path)
    profile = contract.config
    normalization = profile.get('normalization')
    if not isinstance(normalization, dict):
        raise ValueError('Piper normalization is unset; no Robotwin fallback')
    if normalization.get('action_convention') != profile['action_convention']:
        raise ValueError('Normalization action convention mismatch')
    stats_path = (path.parent / normalization['path']).resolve()
    digest = hashlib.sha256(stats_path.read_bytes()).hexdigest()
    if normalization.get('sha256') != digest:
        raise ValueError('Explicit normalization SHA256 mismatch')
    stats = ActionQuantiles.load(stats_path)
    dt, count, fps = (profile.get(k) for k in
                      ('model_action_dt_s', 'model_actions_per_latent', 'model_video_fps'))
    if (not isinstance(count, int) or isinstance(count, bool) or count < 1 or
            dt is None or fps is None or not np.isfinite([dt, fps]).all() or min(dt, fps) <= 0):
        raise ValueError('Explicit model action period, actions/latent and video FPS required')
    if not np.isclose(count * dt, 4 / fps, rtol=1e-5):
        raise ValueError('Timing mismatch: actions_per_latent * action_dt must equal 4/video_fps')
    out = copy.deepcopy(base)
    out['__name__'] = 'Config: Piper explicit physical EEF'
    q = {}
    for name, values in [('q01', stats.q01), ('q99', stats.q99)]:
        full = np.zeros(30);full[USED] = values;q[name] = full.tolist()
    out.update(norm_stat=q, action_stats_path=str(stats_path), action_per_frame=count,
               used_action_channel_ids=USED.copy(), action_dim=30,
               obs_cam_keys=['observation.images.'+k for k in ('cam_high','cam_left_wrist','cam_right_wrist')],
               piper_profile_sha256=contract.digest, piper_profile=profile,
               action_convention=profile['action_convention'], model_action_dt_s=dt,
               model_video_fps=fps, deployment_validated=False)
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', required=True, type=Path)
    parser.add_argument('--check-only', action='store_true')
    args, remaining = parser.parse_known_args()
    if any(a == '--config-name' or a.startswith('--config-name=') for a in remaining):
        parser.error('This entry always uses the Piper configuration')
    # Validate before importing model/CUDA dependencies, including in check-only mode.
    try:
        overrides = configure({}, args.profile)
    except (ValueError, KeyError, OSError) as exc:
        parser.error(str(exc))
    if args.check_only:
        print(json.dumps(overrides, indent=2, default=str));return
    from wan_va.configs.va_robotwin_cfg import va_robotwin_cfg
    config = configure(va_robotwin_cfg, args.profile)
    from wan_va import wan_va_server as server
    server.VA_CONFIGS['piper'] = config
    sys.argv = [sys.argv[0], '--config-name', 'piper', *remaining]
    server.init_logger();server.main()


if __name__ == '__main__':
    main()
