import subprocess,json,hashlib,time,shlex
from pathlib import Path
job=Path(__file__).resolve().parent;root=Path('/media/huawei/Samsung_T5/data/ICL/261020');target='/mnt/sfs_turbo/lijianwen/Datasets/piperpika_10_20';host='root@1.95.69.29';ssh=['ssh','-i','/home/huawei/dgh/29.kem','-o','BatchMode=yes','-o','ConnectTimeout=10'];state={'target':target,'started_at':time.time()}
def status(phase,**kw):
 state.update(phase=phase,**kw);(job/'status.json').write_text(json.dumps(state,ensure_ascii=False,indent=2));print(phase,kw,flush=True)
try:
 folders=['pika_aloha_10_20/lerobot28_v21_h264_crf18_20261020_182114','pika_aloha_10_20_human','pika_aloha_10_20_human_verify','prompt与示教配对_20261020']
 files=sorted(f for n in folders for f in (root/n).rglob('*') if f.is_file());files.append(root/'Zero-WAM_ALOHA_长程任务_含12段验证视频_英文Prompt.xlsx')
 assert all('/hdf5/' not in str(f.relative_to(root)) and '/raw/' not in str(f.relative_to(root)) for f in files)
 remote=subprocess.check_output(ssh+[host,'find '+shlex.quote(target)+' -mindepth 1 -maxdepth 1 -print'],text=True);assert not remote.strip(),'Destination not empty; refusing overwrite'
 status('hashing',files=len(files),bytes=sum(f.stat().st_size for f in files))
 hashes=[];inventory=[]
 for f in files:
  h=hashlib.sha256()
  with f.open('rb') as stream:
   for b in iter(lambda:stream.read(8*1024*1024),b''):h.update(b)
  rel=str(f.relative_to(root));hashes.append(h.hexdigest()+'  '+rel+'\n');inventory.append({'path':rel,'bytes':f.stat().st_size,'sha256':h.hexdigest()})
 (job/'SHA256SUMS').write_text(''.join(hashes));(job/'files.txt').write_text(''.join(str(f.relative_to(root))+'\n' for f in files));(job/'upload_manifest.json').write_text(json.dumps({'files':inventory,'source_root':str(root),'target':target,'robot_data':'LeRobot only; no HDF5/raw','training_human_count':32,'validation_human_count':12,'validation_order_confirmed':False},ensure_ascii=False,indent=2))
 status('uploading')
 with (job/'rsync.log').open('w') as log:
  subprocess.run(['rsync','-rt','--partial','--info=progress2','--files-from='+str(job/'files.txt'),'-e',shlex.join(ssh),str(root)+'/',host+':'+target+'/'],stdout=log,stderr=subprocess.STDOUT,check=True)
 subprocess.run(['rsync','-rt','-e',shlex.join(ssh),str(job/'SHA256SUMS'),str(job/'upload_manifest.json'),host+':'+target+'/'],check=True)
 status('verifying_sha256')
 with (job/'verification.log').open('w') as log:subprocess.run(ssh+[host,'cd '+shlex.quote(target)+' && sha256sum -c SHA256SUMS'],stdout=log,stderr=subprocess.STDOUT,check=True)
 status('complete',completed_at=time.time());subprocess.run(['rsync','-rt','-e',shlex.join(ssh),str(job/'status.json'),host+':'+target+'/upload_status.json'],check=True)
except BaseException as e:status('failed',error=repr(e));raise
