"""Resolve demonstration IDs on the inference host, without shared storage."""
import json
from pathlib import Path


class RegisteredPolicy:
    def __init__(self, policy, registry):
        self.policy = policy
        registry = Path(registry).resolve()
        entries = json.loads(registry.read_text())
        if not isinstance(entries, dict) or not entries:
            raise ValueError('ICL registry must be a nonempty object: ID -> latent path')
        self.latents = {}
        for name, value in entries.items():
            if not isinstance(name, str) or not isinstance(value, str):
                raise ValueError('ICL IDs and paths must be strings')
            path = (registry.parent / value).resolve()
            if not path.is_file():
                raise FileNotFoundError(f'ICL {name}: {path}')
            self.latents[name] = str(path)

    def infer(self, request):
        request = dict(request)
        if request.get('reset') and request.get('use_icl', True):
            name = request.pop('icl_id', None)
            if name not in self.latents:
                raise ValueError(f'Unknown ICL ID {name!r}; available: {list(self.latents)}')
            request.pop('icl_video_path', None)
            request['icl_latent_path'] = self.latents[name]
            request['use_icl'] = True
        return self.policy.infer(request)
