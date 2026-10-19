"""Launch the existing ICL runtime with PiperPika's 12-joint + 2-gripper absolute contract."""
import argparse
from copy import deepcopy
import logging
import json
from datetime import datetime
import uuid
import os
from pathlib import Path


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root', type=Path, default=Path(os.environ.get('PIPERPIKA_PREPARED_ROOT', '/mnt/sfs_turbo/lijianwen/Datasets/piperpika_10_16_zerowam_joint_v2')))
    p.add_argument('--model', type=Path, required=True, help='Complete model root: transformer, vae, tokenizer, text_encoder')
    p.add_argument('--allow-unbound-model', action='store_true', help='Explicitly allow a base/legacy model with no saved PiperPika statistics')
    p.add_argument('--port', type=int, default=29536)
    p.add_argument('--steps', type=int, default=None,
                   help='Set both denoising step counts; overridden by --video-steps or --action-steps')
    p.add_argument('--video-steps', type=int, default=None,
                   help='Video denoising steps (default: num_inference_steps from config)')
    p.add_argument('--action-steps', type=int, default=None,
                   help='Action denoising steps (default: action_num_inference_steps from config)')
    p.add_argument('--chunk-frames', type=int, default=2)
    args = p.parse_args()
    if args.chunk_frames < 2 or any(
        steps is not None and steps < 1
        for steps in (args.steps, args.video_steps, args.action_steps)
    ):
        p.error('Need chunk-frames >= 2 and positive steps')
    logging.basicConfig(level=logging.INFO)
    from wan_va.wan_va_server import VA_Server, init_distributed, run_async_server_mode
    from wan_va.configs.va_piperpika_train_cfg import va_piperpika_train_cfg
    from wan_va.dataset.piperpika import model_quantiles, verify_checkpoint_contract
    contract, bound = verify_checkpoint_contract(args.model, args.root, args.allow_unbound_model)
    cfg = deepcopy(va_piperpika_train_cfg)
    cfg.norm_stat = model_quantiles(args.root / 'train/piperpika/meta/action_stats.json')
    cfg.model_path = str(args.model)
    cfg.frame_chunk_size = args.chunk_frames
    if args.steps is not None:
        cfg.num_inference_steps = cfg.action_num_inference_steps = args.steps
    if args.video_steps is not None:
        cfg.num_inference_steps = args.video_steps
    if args.action_steps is not None:
        cfg.action_num_inference_steps = args.action_steps
    cfg.rank = int(os.environ.get('RANK', 0)); cfg.local_rank = int(os.environ.get('LOCAL_RANK', 0))
    cfg.world_size = int(os.environ.get('WORLD_SIZE', 1))
    init_distributed(cfg.world_size, cfg.local_rank, cfg.rank)
    class PiperPikaServer(VA_Server):
        def infer(self, obs):
            if obs.get('reset'):
                import torch
                seed = int(obs.get('seed', 42))
                torch.manual_seed(seed)
                torch.cuda.manual_seed_all(seed)
            result = super().infer(obs)
            if obs.get('reset'):
                result['action_contract'] = contract['stats'].get('action_contract', 'piperpika_absolute_joint12_gripper2_v1')
                result['checkpoint_contract_bound'] = bound
                result['chunk_frames'] = self.job_config.frame_chunk_size
                result['norm_stat'] = self.job_config.norm_stat
                result['model_path'] = self.job_config.model_path
                result['inference_steps'] = self.job_config.num_inference_steps
                result['video_inference_steps'] = self.job_config.num_inference_steps
                result['action_inference_steps'] = self.job_config.action_num_inference_steps
                result['target_text_enabled'] = self.video_guidance_scale >= 0
                result['video_guidance_scale'] = self.video_guidance_scale
                result['icl_guidance_scale'] = self.icl_guidance_scale
                result['action_order'] = [f'{side}_joint_{i}' for side in ('left', 'right') for i in range(6)] + ['left_gripper', 'right_gripper']
            if obs.get('reset'):
                root = Path(os.environ['ZERO_WAM_PREDICTION_ROOT'])
                self.trace_path = root / ('piperpika_' + datetime.now().strftime('%Y%m%d_%H%M%S_') + uuid.uuid4().hex[:8])
                self.trace_path.mkdir(parents=True, exist_ok=False)
                (self.trace_path / 'reset.json').write_text(json.dumps({'request': obs, 'response': result}, default=str, indent=2))
                self.trace_index = 0
                result['prediction_trace_dir'] = str(self.trace_path)
            elif not obs.get('compute_kv_cache'):
                import torch
                target = self.trace_path / f'chunk_{self.trace_index:05d}.pt'
                temporary = target.with_suffix('.tmp')
                torch.save({'latent': self.last_predicted_latents.detach().cpu(),
                            'action': result['action'], 'chunk_index': self.trace_index,
                            'checkpoint': self.job_config.model_path,
                            'video_fps': int(self.job_config.icl_fps)}, temporary)
                temporary.replace(target)
                result['prediction_artifacts'] = {'latent_path': str(target),
                    'video_path': str(target.with_suffix('.mp4')), 'video_status': 'pending_offline_decode',
                    'chunk_index': self.trace_index}
                self.trace_index += 1
            return result
    model = PiperPikaServer(cfg)
    run_async_server_mode(model, cfg.local_rank, '127.0.0.1', args.port)


if __name__ == '__main__': main()
