"""Offline data checks. No ROS/SDK imports and no control commands."""
import io
import json
from pathlib import Path
import numpy as np


def load_profile(path):
    p = json.loads(Path(path).read_text())
    if not 0 < p['hz'] <= 30 or not 0 < p['max_skew'] <= p['max_age']:
        raise ValueError('Invalid frequency or timestamp limits')
    if p.get('active_arms',['left','right']) not in (['left'], ['right'], ['left','right']):
        raise ValueError('Invalid active_arms')
    return p


def vector_order(p):
    arms=p.get('active_arms',['left','right'])
    return (arms[0]+' joints 1..6, '+arms[0]+' gripper; rad,m') if len(arms)==1 else 'left6,right6,left_gripper,right_gripper; rad,m'


def action_vector(selected, p, kind):
    arms=p.get('active_arms',['left','right'])
    if len(arms)==1:
        return selected[kind+'_'+arms[0]]['message']['position']
    return dual_vector(selected[kind+'_left'],selected[kind+'_right'])


def decode_cameras(payload, p, now):
    with np.load(io.BytesIO(payload), allow_pickle=False) as z:
        stamps = np.array(z['timestamps'], dtype=float)
        if z['serials'].tolist() != p['camera_serials']:
            raise ValueError('Camera serial/role mismatch')
        frames = {k: z[k].copy() for k in p['camera_roles']}
    if stamps.shape != (3,) or not np.isfinite(stamps).all():
        raise ValueError('Invalid camera timestamps')
    if np.any(now-stamps < -.02) or np.any(now-stamps > p['max_age']):
        raise ValueError('Stale/future camera frame')
    if np.ptp(stamps) > p['max_skew']:
        raise ValueError('Camera skew exceeded')
    if any(x.shape != (480,640,3) or x.dtype != np.uint8 for x in frames.values()):
        raise ValueError('Expected 640x480 BGR uint8 images')
    return frames, stamps


def valid_message(row):
    """Validate numeric geometry without reinterpreting raw message units."""
    d = row['message']
    if row['stream']=='control_mode':
        try:
            mode=json.loads(d['data'])
            return mode.get('execute') is True and mode.get('error') is None and mode.get('phase') in ('paused','preparing','active')
        except (KeyError,ValueError,TypeError):
            return False
    if row['stream'].startswith('sense_'):
        return not d.get('error',True) and bool(np.isfinite([d.get('angle',float('nan')),d.get('distance',float('nan'))]).all())
    if row['stream'].startswith(('state_', 'action_')):
        v = np.asarray(d.get('position', []), dtype=float)
        return v.shape == (7,) and bool(np.isfinite(v).all())
    if row['stream'].startswith('pika_'):
        v = list(d['pose']['position'].values()) + list(d['pose']['orientation'].values())
        q = d['pose']['orientation']
        return bool(np.isfinite(v).all()) and abs(sum(q[k]**2 for k in ('x','y','z','w'))-1) < .05
    return True


def select_sample(rows, anchor, tolerance):
    # Latest sample at or before the camera anchor; never substitute a future target.
    eligible = [r for r in rows if 0 <= anchor-r['source_time'] <= tolerance
                and abs(r['receive_time']-r['source_time']) <= tolerance and valid_message(r)]
    return max(eligible, key=lambda r: r['source_time']) if eligible else None


def capture_boundary_ready(rows, anchor, since, profile):
    """Resume only with causal samples actually retained after the active transition."""
    return all(select_sample([r for r in rows[k] if r['receive_time'] >= since],
                             anchor, profile['max_skew']) is not None
               for k in profile['required_topics'])


def dual_vector(left, right):
    l, r = left['message']['position'], right['message']['position']
    return l[:6]+r[:6]+[l[6],r[6]]


def build_index(episode):
    episode = Path(episode)
    meta = json.loads((episode/'manifest.json').read_text())
    p = meta['profile']
    streams = {k: [] for k in p['required_topics']}
    for line in (episode/'messages.jsonl').read_text().splitlines():
        row = json.loads(line)
        if row['stream'] in streams:
            streams[row['stream']].append(row)
    frames = [json.loads(s) for s in (episode/'frames.jsonl').read_text().splitlines()]
    aligned, rejected = [], []
    frame_errors=[]; previous=None
    for frame in frames:
        stamps=np.asarray(frame['source_times'],dtype=float)
        if stamps.shape!=(3,) or not np.isfinite(stamps).all():
            frame_errors.append('Invalid camera timestamps'); continue
        ages=frame['receive_time']-stamps
        if np.any(ages < -.02) or np.any(ages>p['max_age']) or np.ptp(stamps)>p['max_skew']:
            frame_errors.append('Camera timestamp age/skew exceeded')
        if previous is not None and np.any(stamps<=previous):
            frame_errors.append('Repeated/non-increasing camera timestamps')
        previous=stamps
    for frame in frames:
        t = max(frame['source_times'])
        selected = {k: select_sample(v, t, p['max_skew']) for k,v in streams.items()}
        missing = [k for k,v in selected.items() if v is None]
        if missing:
            rejected.append({'frame':frame['file'], 'missing':missing})
            continue
        aligned.append({'time':t, 'frame':frame['file'], 'streams':selected,
                        'state':action_vector(selected,p,'state'),
                        'action':action_vector(selected,p,'action')})
    wall_duration = frames[-1]['receive_time']-frames[0]['receive_time'] if len(frames)>1 else 0
    gaps = np.asarray([b['receive_time']-a['receive_time'] for a,b in zip(frames,frames[1:])
                       if not meta.get('pause_follow_skips_capture') or
                       a.get('capture_segment',0)==b.get('capture_segment',0)])
    duration = float(gaps.sum())
    rate = len(gaps)/duration if duration>0 else 0
    report = {'frames':len(frames), 'aligned':len(aligned), 'rejected':len(rejected),
              'duration_s':duration, 'wall_duration_s':wall_duration, 'achieved_camera_hz':rate,
              'max_camera_gap_s':float(gaps.max()) if len(gaps) else None,
              'rejections':rejected[:20], 'frame_errors':frame_errors[:20], 'capture_status':meta['status']}
    reasons=[]
    if meta['status']!='complete':reasons.append('录制中断：'+str(meta.get('error',meta['status'])))
    if frame_errors:reasons.append('相机时间戳异常：'+frame_errors[0])
    if len(aligned)<10:reasons.append('有效帧不足10帧')
    if len(aligned)!=len(frames):reasons.append(f'数据未对齐：{len(frames)-len(aligned)}帧')
    if rate<p['hz']*.8:reasons.append(f'实际帧率 {rate:.2f} Hz，低于 {p["hz"]*.8:.2f} Hz（目标 {p["hz"]} Hz）')
    if not len(gaps) or gaps.max()>3/p['hz']:reasons.append('相机帧间隔超过允许值')
    report['failure_reasons']=reasons
    report['valid'] = not reasons
    return aligned, report
