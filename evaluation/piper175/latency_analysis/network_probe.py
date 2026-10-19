"""CPU-only WebSocket ACK probe through SSH; does not connect to model or robot."""
import argparse,json,os,time
from pathlib import Path

def main():
 p=argparse.ArgumentParser();p.add_argument('mode',choices=['server','client']);p.add_argument('--port',type=int,default=29058);p.add_argument('--output',default='network_probe.json');a=p.parse_args()
 if a.mode=='server':
  import threading
  from websockets.sync.server import serve
  def handler(ws):
   for message in ws:
    if message==b'PROBE_DONE':
     ws.send(b'BYE');threading.Thread(target=server.shutdown,daemon=True).start();return
    ws.send(str(len(message)).encode())
  with serve(handler,'127.0.0.1',a.port,compression=None,max_size=32*1024*1024,ping_interval=None) as server:
   print('PROBE_READY',flush=True);server.serve_forever()
 else:
  from websockets.sync.client import connect
  results=[];payload=os.urandom(22124036)
  with connect(f'ws://127.0.0.1:{a.port}',compression=None,max_size=32*1024*1024,ping_interval=None) as ws:
   for _ in range(3):ws.send(b'warmup');ws.recv(timeout=20)
   # Alternate resolutions to reduce time-order bias. Small probes bracket transfers.
   sizes=[64]*10+[691709,2765312,5535212,22124036]*5+[64]*10
   for n in sizes:
    block=payload[:n];start=time.perf_counter();ws.send(block);sent=time.perf_counter();reply=ws.recv(timeout=120);end=time.perf_counter()
    assert int(reply)==n
    row=dict(bytes=n,roundtrip_s=end-start,send_call_s=sent-start,receive_wait_s=end-sent,epoch_s=time.time())
    results.append(row);Path(a.output).write_text(json.dumps(results,indent=2));print(json.dumps(row),flush=True)
   ws.send(b'PROBE_DONE');ws.recv(timeout=10)
if __name__=='__main__':main()
