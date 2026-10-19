"""Pair RPC JSONL and server console by request ID; never assume clock sync."""
import argparse
import csv
import json
from pathlib import Path


def merge(client_rows, server_rows):
    index = {}
    for r in server_rows:
        rid = r['request_id']
        if rid in index:
            raise ValueError('Duplicate server request ID: ' + rid)
        index[rid] = r
    result = []
    for c in client_rows:
        rid = c.get('request_id')
        s = index.get(rid)
        row = dict(request_id=rid, kind=c['kind'], total_s=c['total_s'], matched=s is not None)
        if s is not None:
            if c['kind'] != s['kind']:
                raise ValueError('Request kind mismatch')
            transport = c['send_wait_receive_s'] - s['server_received_to_send_s']
            row.update(client_pack_s=c['pack_s'], client_unpack_s=c['unpack_s'],
                       server_unpack_s=s['request_unpack_s'], server_policy_s=s['policy_s'],
                       server_pack_s=s['response_pack_s'],
                       server_other_s=s['server_received_to_send_s']-s['request_unpack_s']-s['policy_s']-s['response_pack_s'],
                       transport_and_scheduling_s=transport,
                       server_send_call_s=s['send_call_s'])
        result.append(row)
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('client_jsonl',type=Path);p.add_argument('server_log',type=Path);p.add_argument('output_csv',type=Path)
    a=p.parse_args()
    clients=[json.loads(l) for l in a.client_jsonl.read_text().splitlines() if l.strip()]
    servers=[json.loads(l.split('RPC_TIMING ',1)[1]) for l in a.server_log.read_text().splitlines() if 'RPC_TIMING ' in l]
    rows=merge(clients,servers)
    keys=list(dict.fromkeys(k for r in rows for k in r))
    with a.output_csv.open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=keys);w.writeheader();w.writerows(rows)
    print(f'{len(rows)} requests; {sum(r["matched"] for r in rows)} matched')

if __name__=='__main__': main()
