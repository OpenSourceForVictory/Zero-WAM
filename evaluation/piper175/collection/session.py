"""Enter: start/save an episode; d: discard current; l/r/p: select/pause; q: quit.

Controller must be running separately. Ending recording does not stop control.
"""
import argparse
import fcntl
import json
from pathlib import Path
import signal
import subprocess
import sys
import time
from export_aloha import export

ROOT=Path(__file__).resolve().parent


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--task',required=True,help='Task instruction')
    parser.add_argument('--output',required=True,help='Task directory')
    args=parser.parse_args()
    if not args.task.strip():parser.error('Task cannot be empty')
    task=Path(args.output).expanduser().resolve();raw=task/'raw';hdf=task/'hdf5'
    raw.mkdir(parents=True,exist_ok=True);hdf.mkdir(exist_ok=True)
    lock=(ROOT/'.session.lock').open('w');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    child=None;log=None;before=set()
    def finish(save):
        nonlocal child,log
        if child is None:return
        if child.poll() is None:child.send_signal(signal.SIGINT)
        try:code=child.wait(timeout=30)
        except subprocess.TimeoutExpired:
            raise RuntimeError('Recorder still exiting; inspect its log, no export attempted')
        child=None;log.close();log=None
        episodes=set(raw.glob('episode_*'))-before
        if len(episodes)!=1:
            print('No unique episode created; check recorder log.');return
        episode=episodes.pop()
        (episode/'review.json').write_text(json.dumps({'accepted':bool(save and code==0),'reviewed_at':time.time()}))
        if not save or code:
            print(f'Not exported (discarded or recorder failed): {episode}');return
        indices=[int(x.stem.split('_')[-1]) for x in hdf.glob('episode_*.hdf5') if x.stem.split('_')[-1].isdigit()]
        dest=hdf/f'episode_{max(indices,default=-1)+1}.hdf5'
        print('Saved '+export(episode,dest),flush=True)
    print('Enter=start/save; d=discard current; l=left; r=right; p=pause; q=quit. Stop recorder != stop robot.',flush=True)
    try:
        while True:
            if child and child.poll() is not None:finish(False)
            cmd=input('recording> ' if child else 'idle> ').strip().lower()
            if cmd in ('l','r','p'):
                subprocess.run([sys.executable,str(ROOT/'switch_control.py'),{'l':'left','r':'right','p':'pause'}[cmd]],check=False)
            elif cmd=='':
                if child:finish(True)
                else:
                    before=set(raw.glob('episode_*'))
                    logfile=task/(time.strftime('record_%Y%m%d_%H%M%S')+'.log')
                    log=logfile.open('x')
                    child=subprocess.Popen(['bash',str(ROOT/'run.sh'),'--profile',str(ROOT/'profile_single_sense_dual.json'),
                                            'record','--task',args.task,'--output',str(raw)],stdout=log,stderr=subprocess.STDOUT)
                    deadline=time.monotonic()+12
                    while child.poll() is None and time.monotonic()<deadline:
                        if 'Recording ' in logfile.read_text():break
                        time.sleep(.1)
                    if 'Recording ' in logfile.read_text():print('Recording started. Demonstrate now; Enter saves, d discards.',flush=True)
                    else:
                        print(f'Recorder not ready; see {logfile}',flush=True)
                        finish(False)
            elif cmd=='d':finish(False)
            elif cmd=='q':
                finish(False);break
            else:print('Use Enter, d, l, r, p, q')
    except (EOFError,KeyboardInterrupt):
        finish(False)
    finally:
        if child:finish(False)
    print('Collection session ended. Run bash switch.sh pause or stop to stop teleoperation.')


if __name__=='__main__':main()
