import logging
import time
from typing import Dict, Optional, Tuple

from typing_extensions import override
import websockets.sync.client
from .msgpack_numpy import Packer, unpackb


class WebsocketClientPolicy:
    """Implements the Policy interface by communicating with a server over websocket.

    See WebsocketPolicyServer for a corresponding server implementation.
    """

    def __init__(self, host: str = "127.0.0.1", port: Optional[int] = None, api_key: Optional[str] = None,
                 connect_timeout: float = 60.0, request_timeout: float = 300.0) -> None:
        self._uri = f"ws://{host}"
        if port is not None:
            self._uri += f":{port}"
        if connect_timeout <= 0 or request_timeout <= 0:
            raise ValueError("Timeouts must be positive")
        self._connect_timeout = connect_timeout
        self._request_timeout = request_timeout
        self._packer = Packer()
        self._api_key = api_key
        self._ws, self._server_metadata = self._wait_for_server()

    def get_server_metadata(self) -> Dict:
        return self._server_metadata

    def _wait_for_server(self) -> Tuple[websockets.sync.client.ClientConnection, Dict]:
        logging.info(f"Waiting for server at {self._uri}...")
        deadline = time.monotonic() + self._connect_timeout
        while True:
            conn = None
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"Could not connect to {self._uri}")
            try:
                headers = {"Authorization": f"Api-Key {self._api_key}"} if self._api_key else None
                # 禁用 ping 机制，防止推理时间过长导致超时
                conn = websockets.sync.client.connect(
                    self._uri, 
                    compression=None, 
                    max_size=None, 
                    additional_headers=headers,
                    ping_interval=None, 
                    open_timeout=min(10, remaining),
                    close_timeout=1
                )
                metadata = unpackb(conn.recv(timeout=max(0.001, deadline - time.monotonic())))
                return conn, metadata
            except Exception as e:
                logging.info(f"Still waiting for server... (Error: {e})")
                if conn is not None:
                    conn.close()
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(f"Could not connect to {self._uri}") from e
                time.sleep(min(1, remaining))

    @override
    def infer(self, obs: Dict) -> Dict:  # noqa: UP006
        data = self._packer.pack(obs)
        self._ws.send(data)
        try:
            response = self._ws.recv(timeout=self._request_timeout)
        except TimeoutError:
            self.close()
            raise TimeoutError("Inference timed out; connection closed. Start a new session and reset before retrying.")
        if isinstance(response, str):
            # we're expecting bytes; if the server sends a string, it's an error.
            raise RuntimeError(f"Error in inference server:\n{response}")
        return unpackb(response)

    @override
    def reset(self) -> None:
        self.infer(dict(reset=True))

    def close(self) -> None:
        self._ws.close()
