"""Bounded single-Sense ROS input check; launches sensors only, no arm nodes."""
import json
import os
from pathlib import Path
import signal
import subprocess
import time

from collect import Observer
from core import load_profile


def main():
    root=Path(__file__).resolve().parent
    profile=root/'profile_single_left.json'
    p=load_profile(profile)
    dest=root/'acceptance'/time.strftime('single_inputs_%Y%m%d_%H%M%S')
    dest.mkdir()
    obs=Observer(p)
    child=None
    try:
        with (dest/'sensors.log').open('w') as log:
            child=subprocess.Popen(['bash',str(root/'run.sh'),'--profile',str(profile),
                                    'sensors','--execute'],stdout=log,stderr=subprocess.STDOUT,
                                   start_new_session=True)
            deadline=time.monotonic()+15
            while time.monotonic()<deadline and child.poll() is None:
                time.sleep(.1)
            issues=obs.readiness(p,keys=['pika_left','sense_left'])
            with obs.lock:
                rows={k:list(obs.rows[k]) for k in ('pika_left','sense_left')}
            result={'counts':{k:len(v) for k,v in rows.items()},'issues':issues,
                    'early_exit':child.poll(),'passed':not issues and child.poll() is None,
                    'scope':'input stream only; button response and arm control not validated'}
            grip=rows['sense_left']
            result['gripper_errors']=sum(bool(r['message'].get('error')) for r in grip)
            if result['gripper_errors']: result['passed']=False
            for field in ('angle','distance'):
                values=[r['message'][field] for r in grip]
                result[field+'_range']=[min(values),max(values)] if values else None
            (dest/'messages.json').write_text(json.dumps(rows))
            (dest/'result.json').write_text(json.dumps(result,indent=2))
            print(json.dumps({'directory':str(dest),**result},indent=2),flush=True)
    finally:
        if child and child.poll() is None:
            os.killpg(child.pid,signal.SIGINT)
            try: child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid,signal.SIGTERM)
                try: child.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    os.killpg(child.pid,signal.SIGKILL); child.wait()
        obs.close()


if __name__=='__main__': main()
