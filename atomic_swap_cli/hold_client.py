"""Python client for the BoltzExchange/hold CLN plugin's gRPC API.

The `hold` plugin (https://github.com/BoltzExchange/hold) lets a CLN node
accept an HTLC for an invoice *without* auto-settling it: the invoice sits
in the `ACCEPTED` state until something calls `Settle` with the preimage,
or `Cancel` to fail it back. That's exactly the "hold" primitive a
submarine swap needs for real atomicity: whichever side reveals the
preimage first (by settling its own hold invoice) enables the other side
to claim theirs with the *same* preimage, and either side can be
cancelled/refunded on timeout before that happens.

This module implements a thin, fully real gRPC client against the
plugin's published `hold.proto` (vendored in `atomic_swap_cli/proto/`).
It requires:
  1. The plugin's Rust binary built and loaded into lightningd
     (`--plugin=/path/to/hold`), which exposes the gRPC server.
  2. Mutual TLS: the plugin always requires client-cert auth and
     self-generates a CA + server + client cert/key set under
     `<lightning-dir>/<network>/hold/{ca,server,client}[-key].pem` the
     first time it starts. `HoldClient.from_cert_dir()` below loads
     those files directly so the demo scripts never need to disable
     TLS (there is no such option in this plugin version).

Building step (1): see `infra/build_hold_plugin.sh` and the README for
the exact commands; a Rust toolchain + protoc are required. This
project's own sandbox successfully built the plugin from source (see
`infra/hold-plugin/`) using that script.
"""
from __future__ import annotations

import binascii
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Optional

import grpc

from .proto import hold_pb2, hold_pb2_grpc


def _hex_to_bytes(value: str) -> bytes:
    return binascii.unhexlify(value)


def _bytes_to_hex(value: bytes) -> str:
    return binascii.hexlify(value).decode()


@dataclass
class HoldInvoiceHandle:
    bolt11: str
    payment_hash: str


@dataclass
class HoldInvoiceStatus:
    payment_hash: str
    state: str  # "UNPAID" | "ACCEPTED" | "PAID" | "CANCELLED"
    bolt11: str
    preimage: Optional[str]


_STATE_NAMES = {
    hold_pb2.UNPAID: "UNPAID",
    hold_pb2.ACCEPTED: "ACCEPTED",
    hold_pb2.PAID: "PAID",
    hold_pb2.CANCELLED: "CANCELLED",
}


class HoldClient:
    """Wraps a grpc channel + HoldStub for one CLN node's hold plugin.

    The plugin always serves mTLS, so the normal path is
    `HoldClient.from_cert_dir(host, port, cert_dir)` where `cert_dir` is
    `<lightning-dir>/<network>/hold`. `insecure=True` is kept only for
    tests that spin up a plain (non-TLS) in-process fake server.
    """

    def __init__(
        self,
        host: str,
        port: int,
        *,
        ca_cert: Optional[bytes] = None,
        client_cert: Optional[bytes] = None,
        client_key: Optional[bytes] = None,
        insecure: bool = False,
    ):
        target = f"{host}:{port}"
        if insecure:
            # Tests / non-TLS fake servers only; the real plugin has no
            # option to disable TLS.
            self._channel = grpc.insecure_channel(target)
        else:
            if ca_cert is None or client_cert is None or client_key is None:
                raise ValueError(
                    "ca_cert/client_cert/client_key are required for the real hold plugin "
                    "(mutual TLS); use HoldClient.from_cert_dir(...) to load them from disk."
                )
            creds = grpc.ssl_channel_credentials(
                root_certificates=ca_cert,
                private_key=client_key,
                certificate_chain=client_cert,
            )
            self._channel = grpc.secure_channel(target, creds)
        self._stub = hold_pb2_grpc.HoldStub(self._channel)

    @classmethod
    def from_cert_dir(cls, host: str, port: int, cert_dir: Path | str) -> "HoldClient":
        """Load the plugin's self-generated CA/client cert+key from
        `<lightning-dir>/<network>/hold` and open an mTLS channel.
        """
        cert_dir = Path(cert_dir)
        return cls(
            host,
            port,
            ca_cert=(cert_dir / "ca.pem").read_bytes(),
            client_cert=(cert_dir / "client.pem").read_bytes(),
            client_key=(cert_dir / "client-key.pem").read_bytes(),
        )

    def close(self) -> None:
        self._channel.close()

    def __enter__(self) -> "HoldClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def get_info(self) -> str:
        resp = self._stub.GetInfo(hold_pb2.GetInfoRequest())
        return resp.version

    def create_invoice(
        self,
        payment_hash: str,
        amount_msat: int,
        *,
        memo: str = "",
        expiry: Optional[int] = None,
        min_final_cltv_expiry: Optional[int] = None,
    ) -> HoldInvoiceHandle:
        """Create a hold invoice locked to an externally-chosen
        `payment_hash` (hex). This is what lets both legs of a swap share
        the same payment hash without either CLN node knowing the
        preimage up front.
        """
        req = hold_pb2.InvoiceRequest(
            payment_hash=_hex_to_bytes(payment_hash),
            amount_msat=amount_msat,
            memo=memo,
        )
        if expiry is not None:
            req.expiry = expiry
        if min_final_cltv_expiry is not None:
            req.min_final_cltv_expiry = min_final_cltv_expiry
        resp = self._stub.Invoice(req)
        return HoldInvoiceHandle(bolt11=resp.bolt11, payment_hash=payment_hash)

    def get_status(self, payment_hash: str) -> HoldInvoiceStatus:
        req = hold_pb2.ListRequest(payment_hash=_hex_to_bytes(payment_hash))
        resp = self._stub.List(req)
        if not resp.invoices:
            raise LookupError(f"no hold invoice found for payment_hash={payment_hash}")
        inv = resp.invoices[0]
        return HoldInvoiceStatus(
            payment_hash=_bytes_to_hex(inv.payment_hash),
            state=_STATE_NAMES.get(inv.state, str(inv.state)),
            bolt11=inv.invoice,
            preimage=_bytes_to_hex(inv.preimage) if inv.HasField("preimage") else None,
        )

    def settle(self, preimage: str) -> None:
        """Release the preimage, settling the hold invoice. This is the
        atomic "unlock" step: revealing `preimage` here also lets the
        counterparty claim the mirrored invoice on the other chain.
        """
        self._stub.Settle(hold_pb2.SettleRequest(payment_preimage=_hex_to_bytes(preimage)))

    def cancel(self, payment_hash: str) -> None:
        """Cancel/fail back a hold invoice (used for refund/timeout paths)."""
        self._stub.Cancel(hold_pb2.CancelRequest(payment_hash=_hex_to_bytes(payment_hash)))

    def track(self, payment_hash: str) -> Iterator[str]:
        """Stream state changes for a single invoice (UNPAID/ACCEPTED/PAID/CANCELLED)."""
        req = hold_pb2.TrackRequest(payment_hash=_hex_to_bytes(payment_hash))
        for resp in self._stub.Track(req):
            yield _STATE_NAMES.get(resp.state, str(resp.state))
