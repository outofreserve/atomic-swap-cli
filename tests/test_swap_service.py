"""Unit tests for SwapService (atomic_swap_cli.swap_service).

All CLN/hold interactions are faked so these tests never touch a live
bitcoind/lightningd/hold process.
"""
from dataclasses import dataclass
from typing import Optional

import pytest

from atomic_swap_cli.models import InvalidTransition, SwapState
from atomic_swap_cli.store import SwapStore
from atomic_swap_cli.swap_service import SwapService, sha256_hex


@dataclass
class FakeInvoiceHandle:
    bolt11: str
    payment_hash: str


@dataclass
class FakeStatus:
    payment_hash: str
    state: str
    bolt11: str
    preimage: Optional[str] = None


class FakeHoldClient:
    """In-memory stand-in for HoldClient / the hold plugin's gRPC API."""

    def __init__(self, name: str):
        self.name = name
        self.invoices: dict[str, FakeStatus] = {}

    def create_invoice(self, payment_hash: str, amount_msat: int, **kwargs) -> FakeInvoiceHandle:
        bolt11 = f"lnbcrt{amount_msat}p1{self.name}{payment_hash[:8]}"
        self.invoices[payment_hash] = FakeStatus(
            payment_hash=payment_hash, state="UNPAID", bolt11=bolt11
        )
        return FakeInvoiceHandle(bolt11=bolt11, payment_hash=payment_hash)

    def get_status(self, payment_hash: str) -> FakeStatus:
        return self.invoices[payment_hash]

    def mark_accepted(self, payment_hash: str) -> None:
        self.invoices[payment_hash].state = "ACCEPTED"

    def settle(self, preimage: str) -> None:
        payment_hash = sha256_hex(preimage)
        status = self.invoices[payment_hash]
        status.state = "PAID"
        status.preimage = preimage

    def cancel(self, payment_hash: str) -> None:
        self.invoices[payment_hash].state = "CANCELLED"


class FakeClnClient:
    def __init__(self):
        self.paid = []

    def pay(self, bolt11: str, amount_msat: Optional[int] = None) -> dict:
        self.paid.append(bolt11)
        return {"status": "pending"}


@pytest.fixture
def service() -> SwapService:
    return SwapService(SwapStore(":memory:"))


def test_initiate_creates_swap_and_invoice(service: SwapService):
    alice_hold = FakeHoldClient("alice-sha256")
    swap = service.initiate(
        initiator_chain="sha256",
        responder_chain="blake2b",
        initiator_node="alice-sha256",
        responder_node="bob-blake2b",
        amount_msat_initiator=50_000,
        amount_msat_responder=50_000,
        initiator_hold_client=alice_hold,
    )
    assert swap.state == SwapState.INITIATED
    assert swap.preimage is not None
    assert swap.payment_hash == sha256_hex(swap.preimage)
    assert swap.initiator_invoice is not None
    assert swap.payment_hash in alice_hold.invoices


def test_accept_mirrors_invoice_without_knowing_preimage(service: SwapService):
    alice_hold = FakeHoldClient("alice-sha256")
    initiated = service.initiate(
        initiator_chain="sha256",
        responder_chain="blake2b",
        initiator_node="alice-sha256",
        responder_node="bob-blake2b",
        amount_msat_initiator=50_000,
        amount_msat_responder=75_000,
        initiator_hold_client=alice_hold,
    )

    bob_hold = FakeHoldClient("bob-blake2b")
    accepted = service.accept(
        payment_hash=initiated.payment_hash,
        initiator_chain="sha256",
        responder_chain="blake2b",
        initiator_node="alice-sha256",
        responder_node="bob-blake2b",
        amount_msat_initiator=50_000,
        amount_msat_responder=75_000,
        responder_hold_client=bob_hold,
    )
    assert accepted.state == SwapState.COUNTERPARTY_LOCKED
    assert accepted.preimage is None
    assert accepted.responder_invoice is not None
    assert accepted.payment_hash in bob_hold.invoices


def test_full_swap_lifecycle_settles_both_legs(service: SwapService):
    alice_hold = FakeHoldClient("alice-sha256")
    bob_hold = FakeHoldClient("bob-blake2b")
    bob_pay_client = FakeClnClient()
    alice_pay_client = FakeClnClient()

    # Alice and Bob each keep their own local swap record (separate
    # parties/processes/databases in real life), sharing only the
    # payment_hash and invoices out of band.
    alice_swap = service.initiate(
        initiator_chain="sha256",
        responder_chain="blake2b",
        initiator_node="alice-sha256",
        responder_node="bob-blake2b",
        amount_msat_initiator=50_000,
        amount_msat_responder=50_000,
        initiator_hold_client=alice_hold,
    )
    bob_swap = service.accept(
        payment_hash=alice_swap.payment_hash,
        initiator_chain="sha256",
        responder_chain="blake2b",
        initiator_node="alice-sha256",
        responder_node="bob-blake2b",
        amount_msat_initiator=50_000,
        amount_msat_responder=50_000,
        responder_hold_client=bob_hold,
    )

    # Bob pays Alice's chain-A invoice; Alice pays Bob's chain-B invoice.
    service.pay_counterparty_invoice(alice_swap, alice_swap.initiator_invoice, bob_pay_client)
    service.pay_counterparty_invoice(bob_swap, bob_swap.responder_invoice, alice_pay_client)

    # Simulate both HTLCs landing as ACCEPTED on the hold plugins.
    alice_hold.mark_accepted(alice_swap.payment_hash)
    bob_hold.mark_accepted(alice_swap.payment_hash)

    bob_swap = service.mark_both_locked_if_ready(
        bob_swap,
        own_hold_client=bob_hold,
        own_payment_hash=bob_swap.payment_hash,
        counterparty_status_fn=lambda: alice_hold.get_status(alice_swap.payment_hash),
    )
    assert bob_swap.state == SwapState.BOTH_LOCKED

    alice_swap = service.mark_counterparty_locked(alice_swap)
    alice_swap = service.mark_both_locked_if_ready(
        alice_swap,
        own_hold_client=alice_hold,
        own_payment_hash=alice_swap.payment_hash,
        counterparty_status_fn=lambda: bob_hold.get_status(alice_swap.payment_hash),
    )
    assert alice_swap.state == SwapState.BOTH_LOCKED

    # Alice settles first (she knows the preimage), revealing it.
    alice_swap = service.settle(alice_swap, alice_hold)
    assert alice_swap.state == SwapState.SETTLED
    assert alice_hold.invoices[alice_swap.payment_hash].state == "PAID"

    # Bob observes the revealed preimage and settles his own leg with it.
    revealed_preimage = alice_swap.preimage
    bob_swap = service.settle_with_preimage(bob_swap, revealed_preimage, bob_hold)
    assert bob_swap.state == SwapState.SETTLED
    assert bob_hold.invoices[bob_swap.payment_hash].state == "PAID"


def test_settle_requires_both_locked(service: SwapService):
    alice_hold = FakeHoldClient("alice-sha256")
    swap = service.initiate(
        initiator_chain="sha256",
        responder_chain="blake2b",
        initiator_node="alice-sha256",
        responder_node="bob-blake2b",
        amount_msat_initiator=1000,
        amount_msat_responder=1000,
        initiator_hold_client=alice_hold,
    )
    with pytest.raises(InvalidTransition):
        service.settle(swap, alice_hold)


def test_settle_with_preimage_validates_hash(service: SwapService):
    alice_hold = FakeHoldClient("alice-sha256")
    bob_hold = FakeHoldClient("bob-blake2b")
    swap = service.initiate(
        initiator_chain="sha256",
        responder_chain="blake2b",
        initiator_node="alice-sha256",
        responder_node="bob-blake2b",
        amount_msat_initiator=1000,
        amount_msat_responder=1000,
        initiator_hold_client=alice_hold,
    )
    swap = service.accept(
        payment_hash=swap.payment_hash,
        initiator_chain="sha256",
        responder_chain="blake2b",
        initiator_node="alice-sha256",
        responder_node="bob-blake2b",
        amount_msat_initiator=1000,
        amount_msat_responder=1000,
        responder_hold_client=bob_hold,
    )
    swap.transition(SwapState.BOTH_LOCKED)
    with pytest.raises(ValueError):
        service.settle_with_preimage(swap, "ff" * 32, bob_hold)


def test_refund_cancels_hold_invoice_and_transitions(service: SwapService):
    alice_hold = FakeHoldClient("alice-sha256")
    swap = service.initiate(
        initiator_chain="sha256",
        responder_chain="blake2b",
        initiator_node="alice-sha256",
        responder_node="bob-blake2b",
        amount_msat_initiator=1000,
        amount_msat_responder=1000,
        initiator_hold_client=alice_hold,
    )
    swap = service.refund(swap, alice_hold, reason="counterparty timed out")
    assert swap.state == SwapState.REFUNDED
    assert alice_hold.invoices[swap.payment_hash].state == "CANCELLED"


def test_fail_transitions_to_failed_with_error(service: SwapService):
    alice_hold = FakeHoldClient("alice-sha256")
    swap = service.initiate(
        initiator_chain="sha256",
        responder_chain="blake2b",
        initiator_node="alice-sha256",
        responder_node="bob-blake2b",
        amount_msat_initiator=1000,
        amount_msat_responder=1000,
        initiator_hold_client=alice_hold,
    )
    swap = service.fail(swap, "peer disconnected")
    assert swap.state == SwapState.FAILED
    assert swap.error == "peer disconnected"
