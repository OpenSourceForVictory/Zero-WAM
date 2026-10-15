"""Latch the client software gate without sending any arm controller command.

Stopping command transmission does not cancel an accepted target and is not a
verified position-hold function. Recovery must be explicit, never automatic.
"""
import json
import os
from pathlib import Path
import tempfile


def latch_software_stop(path):
    path = Path(path)
    try:
        config = json.loads(path.read_text())
    except (OSError, ValueError):
        config = {}
    if not isinstance(config, dict):
        config = {}
    config['emergency_stop'] = True
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', dir=path.parent,
                prefix=path.name + '.', suffix='.tmp', delete=False) as f:
            temporary = Path(f.name)
            json.dump(config, f)
            f.write('\n')
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
