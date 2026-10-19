"""Apply timing instrumentation without replacing site-specific server behavior."""
from pathlib import Path
import sys

p = Path(sys.argv[1])
s = p.read_text()
if 'RPC_TIMING ' in s:
    raise SystemExit('Already instrumented; no change')
replacements = [
 ('import http\n', 'import http\nimport json\nimport uuid\n'),
 ('                obs = unpackb(await websocket.recv())', '''                raw_request = await websocket.recv()
                received_at = time.monotonic()
                obs = unpackb(raw_request)
                unpacked_at = time.monotonic()
                request_id = obs.pop('_rpc_request_id', None) or uuid.uuid4().hex
                kind = 'reset' if obs.get('reset') else ('kv' if obs.get('compute_kv_cache') else 'predict')'''),
 ('                    "infer_ms": infer_time * 1000,', '''                    "infer_ms": infer_time * 1000,
                    "request_id": request_id,
                    "request_unpack_ms": (unpacked_at - received_at) * 1000,'''),
 ('                await websocket.send(packer.pack(action))\n                prev_total_time = time.monotonic() - start_time', '''                pack_started = time.monotonic()
                response = packer.pack(action)
                send_started = time.monotonic()
                await websocket.send(response)
                send_finished = time.monotonic()
                prev_total_time = send_finished - start_time
                # Log after send: measuring actual response packing cannot be
                # embedded in that same response without repacking it.
                logger.info('RPC_TIMING %s', json.dumps({
                    'request_id': request_id, 'kind': kind,
                    'request_bytes': len(raw_request), 'response_bytes': len(response),
                    'recv_wait_s': received_at - start_time,
                    'request_unpack_s': unpacked_at - received_at,
                    'policy_s': infer_time,
                    'response_pack_s': send_started - pack_started,
                    'server_received_to_send_s': send_started - received_at,
                    'send_call_s': send_finished - send_started,
                }))''')]
for old, new in replacements:
    if s.count(old) != 1:
        raise SystemExit('Unexpected server source; refusing partial patch: ' + old[:60])
    s = s.replace(old, new)
compile(s, str(p), 'exec')
backup = p.with_name(p.name + '.before_transport_timing')
if backup.exists():
    raise SystemExit('Backup already exists; inspect before retry')
backup.write_text(p.read_text())
p.write_text(s)
print('Patched', p)
