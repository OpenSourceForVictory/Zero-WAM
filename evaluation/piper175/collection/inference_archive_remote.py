"""Decode one exact prediction session on its originating host. No robot operations."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


def emit(detail, done=0, total=0, unit='chunks'):
    print('ARCHIVE_PROGRESS '+json.dumps(dict(detail=detail,done=done,total=total,unit=unit),ensure_ascii=False),flush=True)


def decoder_command(spec, queue, checkpoint):
    command = [sys.executable, '-u']
    if spec.get('decoder_script'):
        script = Path(spec['decoder_script'])
        if not script.is_file():
            raise FileNotFoundError(f'Decoder script missing: {script}')
        command.append(str(script))
    else:
        command.extend(['-m', 'evaluation.piperpika.decode_predictions'])
    command += ['--root', str(queue.parent), '--checkpoint', checkpoint,
                '--device', spec['device'], '--once']
    if spec.get('source_checkpoint'):
        command += ['--source-checkpoint', spec['source_checkpoint']]
    if spec.get('threads'):
        command += ['--threads', str(spec['threads'])]
    return command


def main(spec):
    source = Path(spec['source']).resolve()
    source.relative_to(Path(spec['trace_root']).resolve())
    if source.parent != Path(spec['trace_root']).resolve():
        raise ValueError('Expected a single prediction session')
    out = Path(spec['output']); out.mkdir(parents=True, exist_ok=True)
    files = sorted(source.glob('chunk_*.pt'))
    if not files:
        raise ValueError('No prediction chunks; nothing to decode')
    indices=[int(p.stem.split('_')[-1]) for p in files]
    if indices!=list(range(len(files))):raise ValueError('Prediction chunks are not contiguous from zero')
    reset = json.loads((source/'reset.json').read_text())
    source_checkpoint = reset['response']['model_path']
    checkpoint = spec.get('checkpoint_override', source_checkpoint)
    queue = out/'decode_work'/source.name
    queue.mkdir(parents=True, exist_ok=True)
    # The R8000 CPU decoder only discovers run directories with a reset record.
    shutil.copy2(source/'reset.json', queue/'reset.json')
    for p in files:
        q = queue/p.name
        if not q.exists():
            try: os.link(p, q)
            except OSError: shutil.copy2(p, q)
        # Reuse only a decoder receipt paired with an existing video.
        if spec.get('reuse_decoded', True) and p.with_suffix('.decoded.json').exists() and p.with_suffix('.mp4').exists():
            for ext in ('.mp4', '.decoded.json'):
                shutil.copy2(p.with_suffix(ext), q.with_suffix(ext))
    emit('正在加载 VAE 解码器；首次加载需要等待',sum(p.with_suffix('.decoded.json').exists() for p in queue.glob('chunk_*.pt')),len(files))
    env = os.environ.copy(); env.update(spec.get('env', {}))
    if any(not p.with_suffix('.decoded.json').exists() or not p.with_suffix('.mp4').exists() for p in queue.glob('chunk_*.pt')):
        process=subprocess.Popen(decoder_command(spec, queue, checkpoint), cwd=spec['code'], env=env)
        while process.poll() is None:
            done=sum((queue/(p.stem+'.decoded.json')).exists() and (queue/(p.stem+'.mp4')).exists() for p in files)
            emit('解码预测视频' if done else '加载 VAE / 等待首个预测视频解码',done,len(files))
            try:process.wait(timeout=2)
            except subprocess.TimeoutExpired:pass
        if process.returncode:raise RuntimeError('Decoder exited with code '+str(process.returncode))
    done=sum((queue/(p.stem+'.decoded.json')).exists() and (queue/(p.stem+'.mp4')).exists() for p in files)
    emit('检查已解码的预测片段',done,len(files))
    if done!=len(files):raise RuntimeError(f'预测解码不完整：{done}/{len(files)}；请检查 decode_work 中的 decode_error.json')
    pred = out/'predictions'; pred.mkdir(exist_ok=True)
    frames = 0
    for p in files:
        receipt = json.loads((queue/(p.stem+'.decoded.json')).read_text())
        frames += receipt['frames']
        for ext in ('.mp4', '.decoded.json'):
            shutil.copy2(queue/(p.stem+ext), pred/(p.stem+ext))
    import imageio_ffmpeg
    ff = imageio_ffmpeg.get_ffmpeg_exe()
    listing = pred/'concat.txt'
    listing.write_text(''.join("file '"+p.stem+".mp4'\n" for p in files))
    video = pred/'prediction_combined.mp4'
    emit('拼接预测视频',len(files),len(files))
    subprocess.run([ff, '-y', '-nostdin', '-v', 'error', '-f', 'concat', '-safe', '0', '-i', str(listing),
                    '-c', 'copy', '-movflags', '+faststart', str(video)], check=True)
    emit('检查拼接视频可完整解码',len(files),len(files))
    subprocess.run([ff, '-nostdin', '-v', 'error', '-i', str(video), '-f', 'null', '-'], check=True)
    shutil.copy2(source/'reset.json', out/'server_reset.json')
    result = dict(prediction_chunks=len(files), prediction_frames=frames, checkpoint=source_checkpoint,
                  server_run=source.name, prediction_bytes=video.stat().st_size,
                  prediction_sha256=hashlib.sha256(video.read_bytes()).hexdigest(), decode_check='passed')
    if checkpoint != source_checkpoint:
        result['decoder_checkpoint'] = checkpoint
    (out/'prediction_verification.json').write_text(json.dumps(result, indent=2))
    print(json.dumps(result), flush=True)


if __name__ == '__main__':
    main(json.loads(sys.argv[1]))
