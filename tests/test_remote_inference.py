"""Transport and demonstration routing tests without model weights or SAPIEN."""
import asyncio
from contextlib import contextmanager
import json
import threading

import numpy as np
import pytest
import websockets.sync.client
from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed

from evaluation.robotwin.websocket_client_policy import WebsocketClientPolicy
from wan_va.utils.Simple_Remote_Infer.deploy.registered_policy import RegisteredPolicy
from wan_va.utils.Simple_Remote_Infer.deploy.websocket_policy_server import WebsocketPolicyServer


class Policy:
    def __init__(self):
        self.requests = []

    def infer(self, request):
        self.requests.append(request)
        return {'action': np.zeros((16, 2, 16), dtype=np.float32)}


@contextmanager
def server(policy):
    ready = threading.Event()
    state = {}

    async def run():
        state['loop'] = asyncio.get_running_loop()
        state['stop'] = asyncio.Event()
        service = WebsocketPolicyServer(policy, single_client=True, metadata={'icl_ids': ['demo']})
        async with serve(service._handler, '127.0.0.1', 0, ping_interval=None) as sock:
            state['port'] = sock.sockets[0].getsockname()[1]
            ready.set()
            await state['stop'].wait()

    thread = threading.Thread(target=lambda: asyncio.run(run()), daemon=True)
    thread.start()
    assert ready.wait(5)
    try:
        yield state['port']
    finally:
        state['loop'].call_soon_threadsafe(state['stop'].set)
        thread.join(5)
        assert not thread.is_alive()


def test_registry_routes_server_path_and_rejects_unknown(tmp_path):
    latent = tmp_path / 'demo.pth'
    latent.touch()
    registry = tmp_path / 'registry.json'
    registry.write_text(json.dumps({'demo': 'demo.pth'}))
    inner = Policy()
    policy = RegisteredPolicy(inner, registry)
    request = dict(reset=True, use_icl=True, icl_id='demo', icl_latent_path='/client/path')
    policy.infer(request)
    assert inner.requests[-1]['icl_latent_path'] == str(latent)
    assert request['icl_latent_path'] == '/client/path'
    with pytest.raises(ValueError, match='Unknown ICL ID'):
        policy.infer(dict(reset=True, icl_id='missing'))
    latent.unlink()
    with pytest.raises(FileNotFoundError):
        RegisteredPolicy(inner, registry)


def test_transport_reset_observation_and_cache():
    policy = Policy()
    with server(policy) as port:
        client = WebsocketClientPolicy(port=port)
        try:
            assert client.get_server_metadata()['icl_ids'] == ['demo']
            client.infer(dict(reset=True))
            rgb = np.zeros((8, 9, 3), dtype=np.uint8)
            result = client.infer(dict(obs=[{'camera': rgb}], compute_kv_cache=True))
            np.testing.assert_array_equal(policy.requests[-1]['obs'][0]['camera'], rgb)
            assert result['action'].shape == (16, 2, 16)
            assert 'server_timing' in result
        finally:
            client.close()


def test_exclusive_session_and_reset_required_after_reconnect():
    with server(Policy()) as port:
        client = WebsocketClientPolicy(port=port)
        client.infer(dict(reset=True))
        try:
            with websockets.sync.client.connect(f'ws://127.0.0.1:{port}') as second:
                with pytest.raises(ConnectionClosed):
                    second.recv(timeout=2)
        finally:
            client.close()
        other = WebsocketClientPolicy(port=port)
        try:
            with pytest.raises(RuntimeError, match='must begin with reset'):
                other.infer(dict(obs=[]))
        finally:
            other.close()


def test_request_timeout_closes_connection():
    class SlowPolicy(Policy):
        def infer(self, request):
            import time
            time.sleep(0.1)
            return super().infer(request)
    with server(SlowPolicy()) as port:
        client = WebsocketClientPolicy(port=port, request_timeout=0.01)
        with pytest.raises(TimeoutError, match='connection closed'):
            client.infer(dict(reset=True))
        with pytest.raises(ConnectionClosed):
            client.infer(dict(reset=True))
