"""Resume the pinned official checkpoint, retrying interrupted files."""
import concurrent.futures
import json
import pathlib
import subprocess
import time
root=pathlib.Path(__file__).resolve().parents[1]
info=json.loads((root/'models/model-info.json').read_text())
target=root/'models/zero-wam-posttrain-robotwin'
revision=info['sha']
sizes={item['path']:item['size'] for item in json.loads((root/'models/manifest.json').read_text()) if item['type']=='file'}

def download(item):
    name=item['rfilename']
    dest=target/name
    dest.parent.mkdir(parents=True,exist_ok=True)
    if dest.exists():
        assert dest.stat().st_size==sizes[name], f'Unexpected file size: {dest}'
        return name
    part=dest.with_name(dest.name+'.part')
    url=f'https://huggingface.co/robbyant-research/zero-wam-posttrain-robotwin/resolve/{revision}/{name}'
    with (root/'logs'/('download-'+name.replace('/','_')+'.log')).open('a') as log:
        for attempt in range(20):
            if part.exists() and part.stat().st_size==sizes[name]:
                break
            result=subprocess.run(['curl','--http1.1','-fL','--connect-timeout','30','--speed-limit','1024','--speed-time','60','-C','-','-o',str(part),url],stdout=subprocess.DEVNULL,stderr=log)
            if result.returncode==0 and part.stat().st_size==sizes[name]:
                break
            print('Retry',name,'attempt',attempt+1,'curl status',result.returncode,flush=True)
            time.sleep(3)
        else:
            raise RuntimeError(f'Download failed after retries: {name}')
    assert part.stat().st_size==sizes[name], f'Incomplete download: {part}'
    part.rename(dest)
    print('Downloaded',name,flush=True)
    return name

with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
    pending=[pool.submit(download,item) for item in info['siblings']]
    # Observe failures as they happen; do not cancel other files if one fails.
    errors=[]
    for future in concurrent.futures.as_completed(pending):
        try: future.result()
        except Exception as exc:
            print('ERROR',repr(exc),flush=True)
            errors.append(exc)
    if errors: raise RuntimeError(f'{len(errors)} downloads failed')
print('Complete',revision,flush=True)
