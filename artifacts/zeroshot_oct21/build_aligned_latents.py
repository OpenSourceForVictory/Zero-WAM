"""Create versioned demo latents whose text follows the verified video action.

Run on the 29 server with its torch environment. Original Vxx.pth files are kept.
The Zero-WAM server substitutes reset prompt embeddings when text_emb is absent.
"""
import hashlib
import json
from pathlib import Path
import sys

import torch


def main(root, rows):
    root = Path(root)
    output = root / 'aligned_prompt'
    output.mkdir(parents=True, exist_ok=True)
    results = []
    for row in rows:
        video_id = row['video_id']
        if not (len(video_id) == 3 and video_id[0] == 'V' and video_id[1:].isdigit()):
            raise ValueError('Invalid video ID')
        source = root / (video_id + '.pth')
        dest = output / (video_id + '_aligned.pth')
        original = torch.load(source, map_location='cpu', weights_only=False)
        if original['source_video'] != row['server_video'] or original['prompt'] != row['old_prompt']:
            raise ValueError(f'{video_id}: source metadata differs from the reviewed manifest')
        corrected = dict(original)
        corrected.pop('text_emb', None)
        corrected.update(original_prompt=original['prompt'], prompt=row['prompt'],
                         text_conditioning='inherit_reset_prompt',
                         alignment_review='2026-10-22 video action order audit')
        if not dest.exists():
            temp = dest.with_suffix('.tmp')
            torch.save(corrected, temp)
            temp.replace(dest)
        check = torch.load(dest, map_location='cpu', weights_only=False)
        if (check['prompt'] != row['prompt'] or check['source_video'] != row['server_video']
                or 'text_emb' in check or not torch.equal(check['latent'], original['latent'])):
            raise ValueError(f'{video_id}: corrected latent failed verification')
        results.append({'video_id': video_id, 'path': str(dest), 'sha256': hashlib.sha256(dest.read_bytes()).hexdigest(),
                        'video_latent_unchanged': True, 'text_conditioning': 'inherit_reset_prompt'})
    (output / 'verification.json').write_text(json.dumps(results, indent=2))
    print(json.dumps(results, indent=2))


if __name__ == '__main__':
    main(sys.argv[1], json.loads(sys.argv[2]))
