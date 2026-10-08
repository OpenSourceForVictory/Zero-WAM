"""Register a local MP4 on the inference host; encoding occurs at episode reset."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import tempfile


def register(registry, demo_id, video):
    import imageio.v2 as imageio

    registry, video = Path(registry).resolve(), Path(video).resolve()
    if not demo_id.strip() or not video.is_file():
        raise ValueError("A nonempty ID and existing video are required")
    with imageio.get_reader(str(video)) as reader:
        frame = reader.get_data(0)
        metadata = reader.get_meta_data()
        if frame.ndim != 3 or frame.shape[2] != 3:
            raise ValueError("Expected an RGB video")
    entries = json.loads(registry.read_text()) if registry.exists() else {}
    if demo_id in entries:
        raise ValueError("Demo ID already exists; choose a new ID for reproducibility")
    entries[demo_id] = {"video": str(video)}
    registry.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", dir=registry.parent, delete=False
    ) as stream:
        json.dump(entries, stream, indent=2)
        stream.write("\n")
        temp = stream.name
    os.replace(temp, registry)
    with video.open("rb") as stream:
        digest = hashlib.sha256()
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return dict(
        id=demo_id,
        video=str(video),
        sha256=digest.hexdigest(),
        fps=metadata.get("fps"),
        size=list(frame.shape),
        registry=str(registry),
    )


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--registry", type=Path, required=True)
    p.add_argument("--id", required=True)
    p.add_argument("--video", type=Path, required=True)
    a = p.parse_args()
    print(json.dumps(register(a.registry, a.id, a.video), indent=2))


if __name__ == "__main__":
    main()
