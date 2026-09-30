"""Minimal JSON-RPC client for bitcoind (regtest). Only the handful of
methods the demo scripts/CLI need are wrapped explicitly; anything else
can be called via `call(method, *params)`.
"""
from __future__ import annotations

import json
import uuid
from typing import Any

import urllib.request
import urllib.error

from .config import BitcoinNodeConfig


class BitcoinRpcError(RuntimeError):
    def __init__(self, code: int, message: str):
        super().__init__(f"bitcoind RPC error {code}: {message}")
        self.code = code
        self.message = message


class BitcoinRpcClient:
    def __init__(self, cfg: BitcoinNodeConfig, timeout: float = 30.0):
        self.cfg = cfg
        self.timeout = timeout

    def call(self, method: str, *params: Any) -> Any:
        payload = json.dumps(
            {"jsonrpc": "1.0", "id": uuid.uuid4().hex, "method": method, "params": list(params)}
        ).encode()
        req = urllib.request.Request(
            f"http://{self.cfg.rpc_host}:{self.cfg.rpc_port}/",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        auth = f"{self.cfg.rpc_user}:{self.cfg.rpc_password}".encode()
        import base64

        req.add_header("Authorization", "Basic " + base64.b64encode(auth).decode())
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            body = json.loads(exc.read())
        if body.get("error"):
            raise BitcoinRpcError(body["error"]["code"], body["error"]["message"])
        return body["result"]

    # --- convenience wrappers -------------------------------------------------

    def getblockchaininfo(self) -> dict:
        return self.call("getblockchaininfo")

    def getnewaddress(self, label: str = "", address_type: str = "bech32") -> str:
        return self.call("getnewaddress", label, address_type)

    def generatetoaddress(self, nblocks: int, address: str) -> list[str]:
        return self.call("generatetoaddress", nblocks, address)

    def getbalance(self) -> float:
        return self.call("getbalance")

    def createwallet(self, name: str = "") -> dict:
        return self.call("createwallet", name)

    def loadwallet(self, name: str) -> dict:
        return self.call("loadwallet", name)

    def sendtoaddress(self, address: str, amount: float) -> str:
        return self.call("sendtoaddress", address, amount)
