"""
VA Server 的 Websocket 客户端
运行环境: piper_new (需要 websockets 和 msgpack)

与 lingbot-va 中的 WebsocketClientPolicy 协议兼容。
"""

import json
import logging
import os
import threading
import time
from pathlib import Path
from stop_request import StopRequested
from typing import Dict, Optional, Tuple

import websockets.sync.client
from msgpack_numpy import Packer, unpackb

_RUNTIME_CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "runtime_config.json"
)


class WebsocketClientPolicy:
    """
    通过 websocket 与 VA Server 通信的策略客户端。
    """

    def __init__(
        self, host: str = "127.0.0.1", port: int = 29536, api_key: Optional[str] = None
    ) -> None:
        self._uri = f"ws://{host}:{port}"
        self._packer = Packer()
        self._api_key = api_key
        self._ws = None
        self._server_metadata = None
        self._lock = threading.Lock()
        self.diagnostics_path = None
        self.last_timing = None

    def _stopped(self):
        try:
            with open(_RUNTIME_CONFIG_PATH) as f:
                return bool(json.load(f).get('emergency_stop', False))
        except (OSError, ValueError):
            return True

    def connect(self) -> Dict:
        if self._stopped():
            raise StopRequested('Runtime stop requested')
        headers = {'Authorization': f'Api-Key {self._api_key}'} if self._api_key else None
        self._ws = websockets.sync.client.connect(self._uri, compression=None,
            max_size=None, additional_headers=headers, ping_interval=None,
            open_timeout=10, close_timeout=3)
        try:
            response = self._ws.recv(timeout=10)
            if isinstance(response, str):
                raise RuntimeError(response)
            self._server_metadata = unpackb(response)
            return self._server_metadata
        except BaseException:
            self.close()
            raise

    def get_server_metadata(self) -> Dict:
        return self._server_metadata

    def infer(self, obs: Dict) -> Dict:
        """发送观测到服务器并获取动作。"""
        with self._lock:
            if self._ws is None:
                raise RuntimeError("Not connected to server. Call connect() first.")
            if self._stopped():
                raise StopRequested('Runtime stop requested before sending to server')

            t0 = time.perf_counter()
            data = self._packer.pack(obs)
            t1 = time.perf_counter()
            self._ws.send(data)
            deadline = time.monotonic() + 300
            while True:
                if self._stopped():
                    raise StopRequested('Runtime stop requested while waiting for server')
                if time.monotonic() >= deadline:
                    raise TimeoutError('Inference server response exceeded 300 seconds')
                try:
                    response = self._ws.recv(timeout=.25)
                    break
                except TimeoutError:
                    continue
            if isinstance(response, str):
                raise RuntimeError(f"Server error: {response}")
            t2 = time.perf_counter()
            result = unpackb(response)
            t3 = time.perf_counter()
            self.last_timing = {'kind': 'reset' if obs.get('reset') else ('kv' if obs.get('compute_kv_cache') else 'predict'),
                'total_s': t3-t0, 'pack_s': t1-t0, 'send_wait_receive_s': t2-t1,
                'unpack_s': t3-t2, 'request_bytes': len(data), 'response_bytes': len(response),
                'server': result.get('diagnostics'), 'endpoint': self._uri}
            if self.diagnostics_path:
                try:
                    with Path(self.diagnostics_path).open('a') as f:
                        f.write(json.dumps(self.last_timing) + '\n')
                except OSError:
                    logging.exception('Could not save client diagnostics')
            return result

    def reset(self, prompt: str, icl_id: Optional[str] = None) -> Dict:
        """Reset LingBot, or explicitly select a registered Zero-WAM demo.

        For Zero-WAM pass icl_id or set ZERO_WAM_ICL_ID. Never silently choose
        a demo: the registry may contain unrelated tasks or codec test videos.
        """
        request = {"reset": True, "prompt": prompt}
        latent_path = os.environ.get("ZERO_WAM_ICL_LATENT_PATH")
        if latent_path:
            if icl_id or os.environ.get("ZERO_WAM_ICL_ID"):
                raise ValueError("Set only one of ZERO_WAM_ICL_LATENT_PATH and ZERO_WAM_ICL_ID")
            if not latent_path.startswith("/") or not latent_path.endswith(".pth"):
                raise ValueError("ZERO_WAM_ICL_LATENT_PATH must be an absolute server .pth path")
            request.update(use_icl=True, icl_latent_path=latent_path)
            return self.infer(request)
        selected = icl_id or os.environ.get("ZERO_WAM_ICL_ID")
        available = (self._server_metadata or {}).get("icl_ids")
        if available is not None:
            if not selected or selected not in available:
                raise ValueError(
                    "Zero-WAM requires an explicit registered demonstration. "
                    "Pass reset(prompt, icl_id=...) or set ZERO_WAM_ICL_ID. "
                    f"Available IDs: {available}"
                )
        if selected:
            request.update(use_icl=True, icl_id=selected)
        return self.infer(request)

    def compute_kv_cache(self, obs_list, state) -> Dict:
        """更新服务器的 KV 缓存。"""
        return self.infer(
            {"obs": obs_list, "compute_kv_cache": True, "state": state}
        )

    def decode_latents(self) -> Dict:
        """解码累积的 latents 并保存视频，返回视频路径。"""
        return self.infer({"decode_latents": True})

    def close(self):
        """Disconnect without clearing a stop requested by the operator."""
        if self._ws:
            self._ws.close()
            self._ws = None
