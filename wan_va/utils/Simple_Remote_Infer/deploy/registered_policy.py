"""Resolve demonstration IDs on the inference host, without shared storage."""

import json
from pathlib import Path


class RegisteredPolicy:
    def __init__(self, policy, registry):
        self.policy = policy
        registry = Path(registry).resolve()
        entries = json.loads(registry.read_text())
        if not isinstance(entries, dict) or not entries:
            raise ValueError(
                "ICL registry must be a nonempty object: ID -> latent path"
            )
        self.latents = {}
        self.demonstrations = {}
        for name, value in entries.items():
            if not isinstance(name, str) or not name:
                raise ValueError("ICL IDs must be nonempty strings")
            entry = {"latent": value} if isinstance(value, str) else value
            if not isinstance(entry, dict) or set(entry) not in ({"video"}, {"latent"}):
                raise ValueError(
                    "ICL entry must be a latent path or {video: path}/{latent: path}"
                )
            kind, value = next(iter(entry.items()))
            if not isinstance(value, str) or not value:
                raise ValueError("ICL path must be a nonempty string")
            path = (registry.parent / value).resolve()
            if not path.is_file():
                raise FileNotFoundError(f"ICL {name}: {path}")
            self.latents[name] = str(path)  # Retained for existing metadata consumers.
            self.demonstrations[name] = {f"icl_{kind}_path": str(path)}

    def infer(self, request):
        request = dict(request)
        if request.get("reset") and request.get("use_icl", True):
            name = request.pop("icl_id", None)
            if name not in self.latents:
                raise ValueError(
                    f"Unknown ICL ID {name!r}; available: {list(self.latents)}"
                )
            request.pop("icl_video_path", None)
            request.pop("icl_latent_path", None)
            request.update(self.demonstrations[name])
            request["use_icl"] = True
        return self.policy.infer(request)
