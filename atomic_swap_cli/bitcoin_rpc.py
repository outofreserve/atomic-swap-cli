"""Minimal JSON-RPC client for bitcoind-family nodes (standard Bitcoin
Core, or the Bitcoin Knots BLAKE2b fork -- same RPC wire format). Only
the handful of methods this project's CLI/safety checks need are
wrapped explicitly; anything else can be called via `call(method,
*params)`.
"""
from __future__ import annotations

import base64
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

    def _auth_header(self) -> str:
        if self.cfg.rpc_user and self.cfg.rpc_password:
            user, password = self.cfg.rpc_user, self.cfg.rpc_password
        elif self.cfg.rpc_cookie_file and self.cfg.rpc_cookie_file.exists():
            # Standard bitcoind cookie auth: "__cookie__:<random>" written
            # to .cookie in the node's datadir on startup. This is the
            # normal way to authenticate against your own real node
            # without configuring a static rpcuser/rpcpassword.
            user, password = self.cfg.rpc_cookie_file.read_text().strip().split(":", 1)
        else:
            raise BitcoinRpcError(
                -1,
                f"no RPC credentials for {self.cfg.label!r}: set rpc_user/rpc_password or "
                f"ensure rpc_cookie_file ({self.cfg.rpc_cookie_file}) exists",
            )
        auth = f"{user}:{password}".encode()
        return "Basic " + base64.b64encode(auth).decode()

    def call(self, method: str, *params: Any) -> Any:
        payload = json.dumps(
            {"jsonrpc": "1.0", "id": uuid.uuid4().hex, "method": method, "params": list(params)}
        ).encode()
        req = urllib.request.Request(
            f"http://{self.cfg.rpc_host}:{self.cfg.rpc_port}/",
            data=payload,
            headers={"Content-Type": "application/json", "Authorization": self._auth_header()},
        )
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

    def getblockcount(self) -> int:
        return self.call("getblockcount")

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

    def gettxout(self, txid: str, vout: int, include_mempool: bool = True) -> dict | None:
        """Returns None if the output is spent/unknown (matches bitcoind's
        own null-on-spent semantics), otherwise the UTXO details
        including ``confirmations``."""
        return self.call("gettxout", txid, vout, include_mempool)

    def getrawtransaction(self, txid: str, verbose: bool = True) -> dict:
        return self.call("getrawtransaction", txid, verbose)

    def gettransaction(self, txid: str) -> dict:
        return self.call("gettransaction", txid)

    def estimatesmartfee(self, conf_target: int = 6) -> dict:
        """Returns e.g. {"feerate": 0.00012345, "blocks": 6} (feerate in
        BTC/kvB). Used to show the user an estimated real fee before
        broadcasting anything on a real-money network."""
        return self.call("estimatesmartfee", conf_target)
