"""Tests for HoldClient (atomic_swap_cli.hold_client) against a real,
in-process gRPC server that implements the `hold.proto` service. This
exercises the actual protobuf wire encoding/decoding and grpc stub
plumbing without needing a live BoltzExchange/hold plugin or lightningd
process.
"""
from concurrent import futures

import grpc
import pytest

from atomic_swap_cli.hold_client import HoldClient
from atomic_swap_cli.proto import hold_pb2, hold_pb2_grpc


class FakeHoldServicer(hold_pb2_grpc.HoldServicer):
    """Minimal in-memory implementation of the hold plugin's gRPC surface."""

    def __init__(self):
        self.invoices: dict[bytes, hold_pb2.Invoice] = {}

    def GetInfo(self, request, context):
        return hold_pb2.GetInfoResponse(version="fake-hold-0.0.0")

    def Invoice(self, request, context):
        bolt11 = f"lnbcrt{request.amount_msat}p1fakehold{request.payment_hash.hex()[:8]}"
        self.invoices[request.payment_hash] = hold_pb2.Invoice(
            id=len(self.invoices) + 1,
            payment_hash=request.payment_hash,
            invoice=bolt11,
            state=hold_pb2.UNPAID,
            created_at=0,
        )
        return hold_pb2.InvoiceResponse(bolt11=bolt11)

    def List(self, request, context):
        inv = self.invoices.get(request.payment_hash)
        if inv is None:
            return hold_pb2.ListResponse(invoices=[])
        return hold_pb2.ListResponse(invoices=[inv])

    def Settle(self, request, context):
        import hashlib

        payment_hash = hashlib.sha256(request.payment_preimage).digest()
        inv = self.invoices.get(payment_hash)
        if inv is not None:
            inv.state = hold_pb2.PAID
            inv.preimage = request.payment_preimage
        return hold_pb2.SettleResponse()

    def Cancel(self, request, context):
        inv = self.invoices.get(request.payment_hash)
        if inv is not None:
            inv.state = hold_pb2.CANCELLED
        return hold_pb2.CancelResponse()


@pytest.fixture
def hold_server():
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=2))
    servicer = FakeHoldServicer()
    hold_pb2_grpc.add_HoldServicer_to_server(servicer, server)
    port = server.add_insecure_port("127.0.0.1:0")
    server.start()
    yield "127.0.0.1", port, servicer
    server.stop(None)


def test_get_info(hold_server):
    host, port, _ = hold_server
    client = HoldClient(host, port, insecure=True)
    assert client.get_info() == "fake-hold-0.0.0"


def test_create_invoice_and_get_status(hold_server):
    host, port, _ = hold_server
    client = HoldClient(host, port, insecure=True)
    payment_hash = "ab" * 32
    handle = client.create_invoice(payment_hash, 100_000, memo="test")
    assert handle.bolt11.startswith("lnbcrt100000p1fakehold")
    status = client.get_status(payment_hash)
    assert status.state == "UNPAID"
    assert status.bolt11 == handle.bolt11


def test_settle_reveals_preimage(hold_server):
    import hashlib

    host, port, _ = hold_server
    client = HoldClient(host, port, insecure=True)
    preimage = "11" * 32
    payment_hash = hashlib.sha256(bytes.fromhex(preimage)).hexdigest()
    client.create_invoice(payment_hash, 50_000)
    client.settle(preimage)
    status = client.get_status(payment_hash)
    assert status.state == "PAID"
    assert status.preimage == preimage


def test_cancel_sets_cancelled_state(hold_server):
    host, port, _ = hold_server
    client = HoldClient(host, port, insecure=True)
    payment_hash = "cc" * 32
    client.create_invoice(payment_hash, 1000)
    client.cancel(payment_hash)
    status = client.get_status(payment_hash)
    assert status.state == "CANCELLED"


def test_get_status_missing_invoice_raises(hold_server):
    host, port, _ = hold_server
    client = HoldClient(host, port, insecure=True)
    with pytest.raises(LookupError):
        client.get_status("ff" * 32)
