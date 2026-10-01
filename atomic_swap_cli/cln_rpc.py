"""JSON-RPC client for Core Lightning's `lightning-rpc` unix domain socket.

This wraps the handful of CLN core methods the demo needs (peer connect,
channel funding, invoice/pay, listpeerchannels). It deliberately avoids
depending on any CLN python SDK so the project only needs the stdlib at
this layer.
"""
from __future__ import annotations

import itertools
import json
import socket
from pathlib import Path
from typing import Any


class ClnRpcError(RuntimeError):
    def __init__(self, code: int, message: str):
        super().__init__(f"CLN RPC error {code}: {message}")
        self.code = code
        self.message = message


class ClnRpcClient:
    """Talks to `<lightning-dir>/<network>/lightning-rpc`.

    Each call opens a short-lived connection, matching the simplicity of
    CLN's own documented examples; this is a demo CLI, not a
    high-throughput service.
    """

    _id_counter = itertools.count(1)

    def __init__(self, socket_path: Path | str):
        self.socket_path = str(socket_path)

    def call(self, method: str, params: dict[str, Any] | list[Any] | None = None) -> Any:
        params = params or {}
        request = {"jsonrpc": "2.0", "id": next(self._id_counter), "method": method, "params": params}
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.connect(self.socket_path)
            sock.sendall(json.dumps(request).encode() + b"\n")
            chunks = []
            while True:
                chunk = sock.recv(65536)
                if not chunk:
                    break
                chunks.append(chunk)
                # CLN responses are newline-delimited JSON objects.
                try:
                    decoded = json.loads(b"".join(chunks))
                    break
                except json.JSONDecodeError:
                    continue
        finally:
            sock.close()
        if not chunks:
            raise ClnRpcError(-1, "empty response from lightningd")
        response = json.loads(b"".join(chunks))
        if "error" in response:
            err = response["error"]
            raise ClnRpcError(err.get("code", -1), err.get("message", str(err)))
        return response["result"]

    # --- convenience wrappers -------------------------------------------------

    def getinfo(self) -> dict:
        return self.call("getinfo")

    def newaddr(self, addresstype: str = "bech32") -> dict:
        return self.call("newaddr", {"addresstype": addresstype})

    def connect(self, node_id: str, host: str, port: int) -> dict:
        return self.call("connect", {"id": node_id, "host": host, "port": port})

    def fundchannel(self, node_id: str, amount_sat: int) -> dict:
        return self.call("fundchannel", {"id": node_id, "amount": amount_sat})

    def listpeerchannels(self, node_id: str | None = None) -> dict:
        params = {"id": node_id} if node_id else {}
        return self.call("listpeerchannels", params)

    def pay(self, bolt11: str, amount_msat: int | None = None) -> dict:
        params: dict[str, Any] = {"bolt11": bolt11}
        if amount_msat is not None:
            params["amount_msat"] = amount_msat
        return self.call("pay", params)

    def decode(self, bolt11: str) -> dict:
        return self.call("decode", {"string": bolt11})

    def waitsendpay(self, payment_hash: str) -> dict:
        return self.call("waitsendpay", {"payment_hash": payment_hash})

    def listinvoices(self, label: str | None = None) -> dict:
        params = {"label": label} if label else {}
        return self.call("listinvoices", params)

    def listfunds(self) -> dict:
        return self.call("listfunds")
