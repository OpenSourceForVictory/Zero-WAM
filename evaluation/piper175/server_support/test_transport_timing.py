import asyncio
import json
import logging
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'wan_va/utils/Simple_Remote_Infer'))
from deploy.websocket_policy_server import WebsocketPolicyServer
from deploy.msgpack_numpy import Packer, unpackb
from websockets.exceptions import ConnectionClosedOK


@pytest.mark.parametrize('payload,kind', [({'reset': True}, 'reset'), ({'compute_kv_cache': True}, 'kv'), ({'obs': {}}, 'predict')])
def test_request_pairing_and_server_boundaries(caplog, payload, kind):
    seen, sent = [], []
    packer = Packer()
    wire = packer.pack(dict(payload, _rpc_request_id='test-request'))
    calls = 0
    async def recv():
        nonlocal calls
        calls += 1
        if calls > 1:
            raise ConnectionClosedOK(None, None)
        return wire
    async def send(data):
        sent.append(unpackb(data))
    ws = SimpleNamespace(remote_address=('test', 1), recv=recv, send=send)
    def infer(obs):
        seen.append(obs)
        return {'ok': True}
    with caplog.at_level(logging.INFO):
        asyncio.run(WebsocketPolicyServer(SimpleNamespace(infer=infer))._handler(ws))
    assert seen == [payload]  # Transport metadata never reaches the model.
    assert sent[1]['server_timing']['request_id'] == 'test-request'
    log = next(x.message.split('RPC_TIMING ', 1)[1] for x in caplog.records if 'RPC_TIMING ' in x.message)
    row = json.loads(log)
    assert row['request_id'] == 'test-request' and row['kind'] == kind
    assert row['policy_s'] == sent[1]['server_timing']['infer_ms'] / 1000
    assert row['server_received_to_send_s'] >= row['request_unpack_s'] + row['policy_s'] + row['response_pack_s']
    assert row['send_call_s'] >= 0
