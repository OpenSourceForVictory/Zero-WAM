#!/usr/bin/env python3
"""Recreate the small prepared metadata required by the R8000 model server."""

import json
from pathlib import Path


MODEL = Path('/data/Zero-WAM/models/piperpika_checkpoint_eval_20261021/best_model')
PREPARED = Path('/data/Zero-WAM/prepared/piperpika_10_16_10_20_zerowam_joint_v1')


def main() -> None:
    contract = json.loads((MODEL / 'piperpika_action_contract.json').read_text())
    for name, key in (
        ('preparation.json', 'preparation'),
        ('train/piperpika/meta/action_stats.json', 'stats'),
    ):
        target = PREPARED / name
        target.parent.mkdir(parents=True, exist_ok=True)
        data = json.dumps(contract[key], ensure_ascii=False, indent=2) + '\n'
        if target.exists() and json.loads(target.read_text()) != contract[key]:
            raise ValueError(f'Existing metadata differs from checkpoint: {target}')
        target.write_text(data)
    print(PREPARED)


if __name__ == '__main__':
    main()
